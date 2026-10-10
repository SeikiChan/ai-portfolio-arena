"""Atomic local shadow books; no broker calls or real orders of any kind."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from zoneinfo import ZoneInfo

from .common import finite, parse_time


def initial_book(cash):
    return {"initial_cash": cash, "cash": cash, "holdings": {}, "pending": None,
            "last_decision": None, "events": [], "equity": [], "stress_cost": 0.0,
            "accounting": "assumption: fractional shadow shares, bid/ask hypothetical execution, price-only PnL; dividends/actual fees unknown"}


def fresh_quote(symbol, quotes, at, config, after=None):
    q = quotes.get(symbol)
    if not q or not all(finite(q.get(k)) for k in ("bp", "ap")):
        raise ValueError(f"{symbol}: missing quote")
    if q["bp"] <= 0 or q["ap"] < q["bp"] or (q["ap"] / q["bp"] - 1) > 0.02:
        raise ValueError(f"{symbol}: invalid / wide quote")
    time = parse_time(q["t"])
    age = (at - time).total_seconds()
    if not 0 <= age <= config["max_quote_age_seconds"]:
        raise ValueError(f"{symbol}: stale or future quote")
    eastern = time.astimezone(ZoneInfo("America/New_York"))
    if eastern.weekday() >= 5 or not (570 <= eastern.hour * 60 + eastern.minute < 960):
        raise ValueError(f"{symbol}: outside regular session")
    if after is not None and time <= after:
        raise ValueError(f"{symbol}: quote predates decision/execution delay")
    return q


def mark(book, quotes, at, config):
    total = book["cash"]
    for symbol, position in book["holdings"].items():
        q = fresh_quote(symbol, quotes, at, config)
        mid = (q["ap"] + q["bp"]) / 2
        # Refuse very large discontinuities; corporate actions require reconciliation.
        if not 0.5 <= mid / position["last_mark"] <= 2:
            raise ValueError(f"{symbol}: corporate action / price discontinuity needs review")
        total += position["shares"] * mid
    return total


def settle(book, quotes, at, config):
    """All-or-nothing local rebalance. Called before issuing a new model decision."""
    out = deepcopy(book)
    plan = out["pending"]
    if not plan:
        return out, "no_pending_plan"
    created = parse_time(plan["completed_at"])
    if at - created > timedelta(hours=config["plan_lifetime_hours"]):
        out["events"].append({"type": "plan_expired", "id": plan["id"], "at": at.isoformat()})
        out["pending"] = None
        return out, "plan_expired"
    after = created + timedelta(seconds=config["minimum_execution_delay_seconds"])
    weights = {p["symbol"]: p["target_weight"] for p in plan["decision"]["positions"] if p["target_weight"] > 0}
    try:
        needed = set(weights) | set(out["holdings"])
        used = {s: fresh_quote(s, quotes, at, config, after) for s in needed}
        equity = mark(out, quotes, at, config)
    except ValueError as exc:
        return out, "pending: " + str(exc)
    if at <= after:
        return out, "pending: minimum execution delay"
    desired = {s: equity * w / used[s]["ap"] for s, w in weights.items()}
    fills = []
    # Sell first; all prices are newly observed after the prior decision.
    for symbol in sorted(needed):
        have = out["holdings"].get(symbol, {}).get("shares", 0)
        difference = desired.get(symbol, 0) - have
        if difference < -1e-8:
            quantity = -difference
            price = used[symbol]["bp"]
            out["cash"] += quantity * price
            fills.append({"symbol": symbol, "side": "SELL", "shares": quantity, "price": price, "quote_time": used[symbol]["t"]})
    for symbol in sorted(needed):
        have = out["holdings"].get(symbol, {}).get("shares", 0)
        difference = desired.get(symbol, 0) - have
        if difference > 1e-8:
            price = used[symbol]["ap"]
            out["cash"] -= difference * price
            fills.append({"symbol": symbol, "side": "BUY", "shares": difference, "price": price, "quote_time": used[symbol]["t"]})
    if out["cash"] < -1e-6:
        return deepcopy(book), "pending: insufficient cash; entire rebalance rejected"
    out["cash"] = max(0.0, out["cash"])
    out["holdings"] = {s: {"shares": qty, "last_mark": (used[s]["ap"] + used[s]["bp"]) / 2}
                       for s, qty in desired.items() if qty > 1e-8}
    stress = sum(f["shares"] * f["price"] for f in fills) * config["stress_cost_bps_per_side"] / 10000
    out["stress_cost"] += stress
    out["events"].append({"type": "shadow_rebalance", "classification": "assumption", "id": plan["id"],
                          "at": at.isoformat(), "fills": fills, "stress_cost_assumption": stress,
                          "feed": config["quote_feed"], "actual_fees": None, "actual_market_impact": None})
    out["pending"] = None
    return out, "shadow_rebalance_recorded"


def record_equity(book, quotes, at, config):
    try:
        value = mark(book, quotes, at, config)
    except ValueError as exc:
        return {"status": "unknown", "reason": str(exc)}
    row = {"at": at.isoformat(), "equity": value, "stress_equity": value - book["stress_cost"],
           "return": value / book["initial_cash"] - 1, "classification": "derived_from_shadow_assumptions"}
    book["equity"].append(row)
    for symbol, position in book["holdings"].items():
        position["last_mark"] = (quotes[symbol]["ap"] + quotes[symbol]["bp"]) / 2
    return {"status": "marked", **row}
