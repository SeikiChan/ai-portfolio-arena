"""Turn a validated target portfolio into Alpaca PAPER orders. The only module that writes to a broker.

The research layer still has no order path. That invariant is not deleted, it is relocated: a model
never names an order, a quantity or a price. It outputs target weights, those weights pass
deterministic validation, and only then does this module compute the difference against the
positions the broker actually reports and submit orders for that difference. The ledger is the
broker's fills, never the model's prose.

Deliberate limits for this first version:

- POST /v2/orders is the single write endpoint. No cancel, no replace, no close-position. An exit is
  a sell down to zero, so the extra endpoints buy nothing and widen the blast radius.
- Dry run is the default. Submission requires an explicit flag.
- client_order_id is derived from (round, provider, symbol, side), so running the same plan twice
  cannot place a second order: the broker rejects the duplicate id. One round places at most one
  order per symbol per side however many times it is run.
- Limit orders, DAY, whole shares. A market order would hand us an unbounded price; a limit priced
  off the current quote caps it and simply expires unfilled if the market moves away, which the next
  round sees as the real position and re-plans from.
- Risk-reducing legs (sells) are submitted even when the risk-increasing legs (buys) cannot be
  afforded yet. Cash from a sell is not available until it fills, so buys that exceed settled cash
  are deferred and recorded, never silently shrunk: shrinking a weight would change the model's
  decision on its behalf.
- Nothing is tracked to completion. The next round reads the broker's positions and re-plans, which
  self-heals partial fills and expiries without a state machine that can disagree with the broker.
"""
from __future__ import annotations

import argparse
import math

import requests

from .common import APP, digest, now, parse_time, read, stamp, write
from .credentials import market_data_keys, paper_credentials
from .paper_accounts import BASE, number, read_one

LIMIT_BUFFER = 0.002        # assumption: 20bp past the touch. Caps the price without chasing.
MIN_TRADE_FRACTION = 0.005  # assumption: skip rebalances below 0.5% of equity, so drift is not churn.
MAX_QUOTE_AGE_SECONDS = 120
MAX_PRICE_DEVIATION = 0.2   # Refuse a quote this far from the packet's last close: gapped or bad.
TIF = "day"


def assert_paper():
    if BASE != "https://paper-api.alpaca.markets":
        raise ValueError("券商地址不是 Paper 域名，已拒绝提交")


def session_open(credentials, getter=None):
    getter = getter or requests.get
    key, secret = credentials
    response = getter(BASE + "/v2/clock", headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
                      timeout=20, allow_redirects=False)
    if response.status_code != 200:
        raise ValueError(f"无法读取交易时段：HTTP {response.status_code}")
    clock = response.json()
    if not clock.get("is_open"):
        raise ValueError(f"当前不在美股常规交易时段（下次开市 {clock.get('next_open')}），不提交委托")
    return clock


def quotes(symbols, getter=None):
    getter = getter or requests.get
    keys = market_data_keys()
    response = getter("https://data.alpaca.markets/v2/stocks/quotes/latest",
                      params={"symbols": ",".join(sorted(symbols)), "feed": "iex"},
                      headers={"APCA-API-KEY-ID": keys["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": keys["APCA_API_SECRET_KEY"]},
                      timeout=20, allow_redirects=False)
    if response.status_code != 200:
        raise ValueError(f"无法读取报价：HTTP {response.status_code}")
    return response.json().get("quotes", {})


def quote_metrics(symbol, quote, last_close, at):
    """Side-independent checks. A quote either supports an order or the whole plan waits."""
    bid, ask = number(quote.get("bp")), number(quote.get("ap"))
    if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
        raise ValueError(f"{symbol} 报价无效，不补价、不用收盘价冒充")
    age = (at - parse_time(quote["t"])).total_seconds()
    if age < 0:
        raise ValueError(f"{symbol} 报价时间在本机时间之后，拒绝使用")
    if age > MAX_QUOTE_AGE_SECONDS:
        raise ValueError(f"{symbol} 报价已过期 {age:.0f} 秒，拒绝使用")
    mid = (bid + ask) / 2
    if last_close and abs(mid / last_close - 1) > MAX_PRICE_DEVIATION:
        raise ValueError(f"{symbol} 现价与冻结收盘价相差超过 {MAX_PRICE_DEVIATION:.0%}，暂停执行并人工核对")
    return {"bid": bid, "ask": ask, "mid": mid, "quote_age_seconds": round(age, 3)}


def limit_for(side, metrics):
    price = metrics["ask"] * (1 + LIMIT_BUFFER) if side == "buy" else metrics["bid"] * (1 - LIMIT_BUFFER)
    return round(price, 2)


def actionable(decision, positions):
    """Symbols that could need an order. A weight-0 watch idea with no position is a no-op: quoting
    it could only ever block a plan it has no part in."""
    held = {p["symbol"] for p in positions if (number(p.get("qty")) or 0) != 0}
    targeted = {p["symbol"] for p in decision["positions"] if p["target_weight"] > 0}
    return held | targeted


def plan(decision, account, positions, quote_map, closes, config, at, tradable=True):
    """Deterministic target-to-order difference. The model supplies weights and nothing else.

    tradable=False prices an indicative plan off the frozen close for inspection outside the session.
    Such a plan can never be submitted; execute() refuses it.
    """
    equity = number(account.get("equity"))
    cash = number(account.get("cash"))
    if account.get("status") != "ACTIVE" or equity is None or equity <= 0 or cash is None or cash < 0:
        raise ValueError("账户状态不支持执行：需要 ACTIVE、正净值、非负现金")
    held = {p["symbol"]: number(p.get("qty")) or 0.0 for p in positions}
    if any(qty < 0 for qty in held.values()):
        raise ValueError("账户存在空头持仓，当前只支持现金做多执行")
    targets = {p["symbol"]: p["target_weight"] for p in decision["positions"]}
    if any(symbol not in config["universe"] for symbol in set(targets) | set(held)):
        raise ValueError("目标或持仓存在股票池之外的标的，已拒绝整份计划")
    if sum(targets.values()) > config["max_invested"] + 1e-9:
        raise ValueError("目标总仓位超过上限，已拒绝整份计划")
    if any(weight > config["max_weight"] + 1e-9 for weight in targets.values()):
        raise ValueError("单股目标权重超过上限，已拒绝整份计划")
    if sum(1 for weight in targets.values() if weight > 0) > config["max_positions"]:
        raise ValueError("正仓位数量超过上限，已拒绝整份计划")
    legs, skipped = [], []
    for symbol in sorted(actionable(decision, positions)):
        if tradable:
            if symbol not in quote_map:
                raise ValueError(f"缺少报价的标的：{symbol}；整份计划暂不执行")
            metrics = quote_metrics(symbol, quote_map[symbol], closes.get(symbol), at)
        else:
            close = closes.get(symbol)
            if not close:
                raise ValueError(f"{symbol} 既无有效报价也无冻结收盘价，无法给出指示性计划")
            metrics = {"bid": close, "ask": close, "mid": close, "quote_age_seconds": None}
        current_qty = held.get(symbol, 0.0)
        # Hold both sides of the book out of the valuation: mid values the position, the touch prices the order.
        delta_value = targets.get(symbol, 0.0) * equity - current_qty * metrics["mid"]
        side = "buy" if delta_value > 0 else "sell"
        # The buffer crosses a real touch. An indicative plan has no touch to cross, so it shows the close.
        limit_price = limit_for(side, metrics) if tradable else round(metrics["mid"], 2)
        quantity = math.floor(abs(delta_value) / limit_price)
        if side == "sell":
            quantity = min(quantity, math.floor(current_qty))
        # A full exit is never dust: the model said the thesis is dead, and the size of the
        # position does not change that. The minimum only suppresses rebalancing drift.
        exiting = targets.get(symbol, 0.0) == 0 and current_qty > 0
        if quantity < 1 or (not exiting and abs(delta_value) < equity * MIN_TRADE_FRACTION):
            skipped.append({"symbol": symbol, "reason": "不足一股，无法下单" if quantity < 1 else
                            "低于最小调整额，视为漂移而非决定",
                            "delta_value": round(delta_value, 2), "target_weight": targets.get(symbol, 0.0)})
            continue
        legs.append({"symbol": symbol, "side": side, "qty": quantity, "limit_price": limit_price,
                     "notional": round(quantity * limit_price, 2), "target_weight": targets.get(symbol, 0.0),
                     "current_qty": current_qty, **{k: metrics[k] for k in ("bid", "ask", "quote_age_seconds")}})
    sells = [leg for leg in legs if leg["side"] == "sell"]
    buys = [leg for leg in legs if leg["side"] == "buy"]
    # Sell proceeds are not settled cash yet, so buys are measured against cash on hand only.
    affordable, deferred, budget = [], [], cash * 0.98
    for leg in sorted(buys, key=lambda leg: -leg["notional"]):
        if leg["notional"] <= budget:
            budget -= leg["notional"]
            affordable.append(leg)
        else:
            deferred.append({**leg, "reason": "现金不足；卖出成交后下一轮重新规划，不缩小权重替模型做决定"})
    return {"equity": equity, "cash": cash, "orders": sells + sorted(affordable, key=lambda leg: leg["symbol"]),
            "deferred": deferred, "skipped": skipped, "planned_at": stamp(), "tradable": tradable,
            "no_op": sorted({p["symbol"] for p in decision["positions"]} - actionable(decision, positions)),
            "classification": {"quotes": "observed", "limit_prices": "derived", "fills": "unknown until the broker reports them"}}


def client_order_id(round_id, provider, symbol, side):
    # Deterministic: re-running a plan cannot place a second order for the same leg.
    return f"arena-{round_id}-{provider}-{symbol}-{side}"[:128]


def submit(leg, credentials, round_id, provider, poster=None):
    assert_paper()
    poster = poster or requests.post
    key, secret = credentials
    payload = {"symbol": leg["symbol"], "qty": str(leg["qty"]), "side": leg["side"], "type": "limit",
               "time_in_force": TIF, "limit_price": f"{leg['limit_price']:.2f}", "extended_hours": False,
               "client_order_id": client_order_id(round_id, provider, leg["symbol"], leg["side"])}
    response = poster(BASE + "/v2/orders", json=payload,
                      headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
                      timeout=25, allow_redirects=False)
    record = {"request": payload, "status_code": response.status_code, "submitted_at": stamp()}
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code in (200, 201):
        record.update(outcome="accepted", order_id=body.get("id"), broker_status=body.get("status"))
    elif response.status_code == 422 and "client_order_id" in str(body.get("message", "")).lower():
        # Already submitted for this round. Idempotency working, not a failure.
        record.update(outcome="duplicate_ignored", broker_message=body.get("message"))
    else:
        record.update(outcome="rejected", broker_message=str(body.get("message", ""))[:300])
    return record


def execute(provider, round_directory, config, submit_orders=False, getter=None, poster=None, at=None):
    assert_paper()
    at = at or now()
    round_directory = __import__("pathlib").Path(round_directory)
    job = read(round_directory / "round.json")
    status = read(round_directory / f"{provider}_status.json")
    if status.get("status") != "validated" or not status.get("decision"):
        raise ValueError("只执行通过校验的决定；失败或缺失的轮次不得成交")
    credentials = paper_credentials(only=provider)[provider]
    mode = "submitted" if submit_orders else "dryrun"
    snapshot = read_one(provider, credentials, round_directory / f"execution_{mode}_{at.strftime('%Y%m%dT%H%M%S%fZ')}_account", getter=getter)
    if snapshot["status"] != "connected":
        raise ValueError("执行前账户读取不完整，拒绝提交")
    bindings = read(round_directory.parent.parent / "bindings.json")
    if bindings.get(provider) and snapshot["account"].get("id") != bindings[provider]:
        raise ValueError(f"{provider} 的账户身份与绑定不符，拒绝提交")
    packet = read(round_directory / "packet.json")
    closes = {row["symbol"]: row.get("last_close") for row in packet.get("features", [])}
    decision = status["decision"]
    symbols = actionable(decision, snapshot["positions"])
    quote_map = quotes(symbols, getter=getter) if symbols else {}
    missing = sorted(symbols - set(quote_map))
    if missing and submit_orders:
        raise ValueError(f"缺少报价的标的：{', '.join(missing)}；整份计划暂不执行")
    session = session_open(credentials, getter=getter) if submit_orders else {"is_open": None, "note": "dry run 不检查时段"}
    try:
        proposal = plan(decision, snapshot["account"], snapshot["positions"], quote_map, closes, config, at)
    except ValueError:
        if submit_orders:
            raise
        # Outside the session there is no tradable price. Show the intent, labelled, rather than nothing.
        proposal = plan(decision, snapshot["account"], snapshot["positions"], quote_map, closes, config, at, tradable=False)
    record = {"round_id": job["id"], "provider": provider, "account_suffix": str(snapshot["account"].get("account_number", ""))[-4:],
              "decision_instruction_sha256": status.get("instruction_sha256"), "packet_id": job.get("packet_id"),
              "broker": BASE, "mode": "submitted" if submit_orders else "dry_run", "session": session,
              "plan": proposal, "results": [], "source_code": {p.name: digest(p) for p in APP.glob("*.py")}}
    if submit_orders:
        if not proposal["tradable"]:
            raise ValueError("指示性计划不可提交：没有可成交报价")
        for leg in proposal["orders"]:
            record["results"].append(submit(leg, credentials, job["id"], provider, poster=poster))
    record["orders_submitted"] = bool(record["results"])
    record["completed_at"] = stamp()
    write(round_directory / f"execution_{mode}_{at.strftime('%Y%m%dT%H%M%S%fZ')}.json",
          record, exclusive=True)
    return record


def latest_validated(root, provider):
    for directory in sorted((root / "rounds").glob("*"), reverse=True):
        path = directory / f"{provider}_status.json"
        if path.exists() and read(path).get("status") == "validated":
            return directory
    raise ValueError(f"没有找到 {provider} 的已校验轮次")


def main():
    parser = argparse.ArgumentParser(description="Submit a validated target portfolio to Alpaca PAPER.")
    parser.add_argument("--provider", choices=("claude", "gpt"), required=True)
    parser.add_argument("--round", help="轮次目录；默认该模型最近一次通过校验的轮次")
    parser.add_argument("--submit", action="store_true", help="真正提交委托。缺省只做 dry run")
    arguments = parser.parse_args()
    from .console_store import CONSOLE_DATA
    config = read(APP / "config.json")
    directory = __import__("pathlib").Path(arguments.round) if arguments.round else latest_validated(CONSOLE_DATA, arguments.provider)
    try:
        record = execute(arguments.provider, directory, config, submit_orders=arguments.submit)
    except ValueError as exc:
        raise SystemExit(f"拒绝执行：{exc}")
    print(__import__("json").dumps({k: record[k] for k in ("round_id", "provider", "mode", "orders_submitted")}, ensure_ascii=False))
    print(f"净值 ${record['plan']['equity']:,.2f} · 现金 ${record['plan']['cash']:,.2f}"
          + ("" if record["plan"]["tradable"] else " · 指示性计划：按冻结收盘价估算，当前无可成交报价，不可提交"))
    for leg in record["plan"]["orders"]:
        print(f"  {leg['side']:4s} {leg['qty']:>6} {leg['symbol']:6s} limit {leg['limit_price']:>10,.2f} "
              f"= ${leg['notional']:>12,.2f}  目标 {leg['target_weight']:.0%}")
    for leg in record["plan"]["deferred"]:
        print(f"  推迟 {leg['symbol']}: {leg['reason']}")
    for leg in record["plan"]["skipped"]:
        print(f"  跳过 {leg['symbol']}: {leg['reason']}")
    if record["plan"]["no_op"]:
        print(f"  无需下单（0 权重且无持仓）: {', '.join(record['plan']['no_op'])}")
    for row in record["results"]:
        print(f"  -> {row['request']['symbol']} {row['outcome']} {row.get('broker_status') or row.get('broker_message') or ''}")
    if not arguments.submit:
        print("\ndry run：没有向券商提交任何委托。确认无误后加 --submit。")


if __name__ == "__main__":
    main()
