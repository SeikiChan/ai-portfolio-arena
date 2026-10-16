"""GET-only adapter for two distinct Alpaca Paper accounts. No order mutations."""
from __future__ import annotations

import re
import time as wait
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .common import APP, digest, finite, external_reference, stamp, write
from .credentials import paper_credentials, parse

BASE = "https://paper-api.alpaca.markets"
PREFIX = {"claude": "Claude", "gpt": "Gpt"}
ENDPOINTS = {
    "account": ("/v2/account", {}),
    "positions": ("/v2/positions", {}),
    "orders": ("/v2/orders", {"status": "all", "limit": 100, "direction": "desc", "nested": "true"}),
}
# A snapshot needs all three endpoints, and one transient failure on one of them used to cost a
# whole trading day: on 2026-10-16 positions and orders came back, /v2/account did not, so the
# pre-execution snapshot read "unavailable" and a decision that had passed validation was refused.
# Retry only where retrying can help. Bad credentials are not transient, so a 401/403 is returned
# on the first answer rather than repeated three times against a rate limit.
READ_ATTEMPTS = 3
READ_BACKOFF_SECONDS = 0.75
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
SHAPE = {"account": lambda payload: isinstance(payload, dict) and bool(payload.get("id"))}


def fetch_endpoint(getter, url, params, headers, check, attempts=READ_ATTEMPTS, sleep=None):
    """One endpoint as (payload, problem, attempts_used). Exactly one of payload/problem is set.

    A truncated body raises inside .json() and is retried for the same reason a 503 is: the next
    answer may be whole. Nothing here is written to disk, so a retry cannot collide with the
    exclusive writes that record a successful read.
    """
    sleep = sleep or wait.sleep
    problem = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = getter(url, headers=headers, params=params, timeout=20, allow_redirects=False)
            if response.status_code != 200:
                problem = f"Alpaca Paper HTTP {response.status_code}"
                if response.status_code not in RETRYABLE_STATUS:
                    return None, problem, attempt
            else:
                payload = response.json()
                if not check(payload):
                    raise ValueError("Unexpected response shape")
                return payload, None, attempt
        except requests.RequestException as exc:
            problem = type(exc).__name__
        except ValueError as exc:
            problem = str(exc)[:60] or type(exc).__name__
        if attempt < max(1, attempts):
            sleep(READ_BACKOFF_SECONDS * attempt)
    return None, problem, max(1, attempts)


def load_credentials(path=None, only=None):
    if path is not None:
        return _from_file(Path(path), only)
    return paper_credentials(only)


def _from_file(path, only=None):
    values = parse(path)
    if not values:
        raise ValueError(f"未找到 {path.name}")
    result = {}
    for name in ([only] if only else list(PREFIX)):
        prefix = PREFIX[name]
        key, secret = values.get(prefix + "APCA_API_KEY_ID"), values.get(prefix + "APCA_API_SECRET_KEY")
        if not key or not secret:
            raise ValueError(f"{name} 的 Paper 凭据尚未完整配置")
        result[name] = (key, secret)
    if not only and len({pair[0] for pair in result.values()}) != len(result):
        raise ValueError("两个模型使用了相同 API Key，请配置不同 Paper 账户")
    return result


def read_one(name, credentials, directory, getter=None):
    getter = getter or requests.get
    key, secret = credentials
    result = {"provider": name, "environment": "alpaca_paper", "status": "connected", "fetched_at": stamp(), "errors": [], "records": []}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    recovered = []
    for kind, (path, params) in ENDPOINTS.items():
        check = SHAPE.get(kind, lambda payload: isinstance(payload, list))
        payload, problem, attempts = fetch_endpoint(getter, BASE + path, params, headers, check)
        if problem is not None:
            result["errors"].append({"source": kind, "error": problem, "attempts": attempts})
            continue
        if attempts > 1:
            # Deliberately not in errors: any entry there downgrades status out of "connected",
            # which would turn every blip this retry exists to absorb into a refusal instead.
            recovered.append({"source": kind, "attempts": attempts})
        file = directory / (name + "_" + kind + ".json")
        write(file, payload, exclusive=True)
        result[kind] = payload
        result["records"].append({"file": file.name, "source": "GET " + BASE + path, "query": params,
                                   "rows": len(payload) if isinstance(payload, list) else 1, "sha256": digest(file), "classification": "observed"})
    if recovered:
        result["recovered_reads"] = recovered
    if "account" not in result:
        result["status"] = "unavailable"
    elif result["errors"]:
        result["status"] = "partial"
    result["fetched_at"] = stamp()
    return result


def verify_bindings(accounts, pinned=None):
    pinned = pinned or {}
    identities = {name: item.get("account", {}).get("id") for name, item in accounts.items()}
    present = [value for value in identities.values() if value]
    if len(present) != len(set(present)):
        raise ValueError("两个模型连接到同一个 Paper 账户，已拒绝绑定")
    for name, expected in pinned.items():
        if identities.get(name) and identities[name] != expected:
            raise ValueError(f"{name} 的 Paper 账户身份改变，需另建实验；禁止沿用旧记录")
    return {**pinned, **{k: v for k, v in identities.items() if v}}


def capture(directory, pinned=None, credentials=None):
    credentials = credentials or load_credentials()
    directory.mkdir(parents=True, exist_ok=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {name: pool.submit(read_one, name, credentials[name], directory) for name in PREFIX}
        accounts = {name: task.result() for name, task in futures.items()}
    bindings = verify_bindings(accounts, pinned)
    snapshot = {"snapshot_id": directory.name, "fetched_at": stamp(), "accounts": accounts,
                "classification": "observed: Alpaca Paper simulation account, not live money", "orders_submitted": False,
                "truth_reference": external_reference(), "source_code": {str(p.relative_to(APP)): digest(p) for p in APP.rglob('*') if p.is_file() and p.suffix in {'.py','.js','.css','.html'}}}
    write(directory / "snapshot.json", snapshot, exclusive=True)
    return snapshot, bindings


def number(value):
    try:
        number = float(value)
        return number if finite(number) else None
    except (ValueError, TypeError):
        return None


def public_account(item):
    account = item.get("account", {})
    result = {"provider": item["provider"], "environment": "alpaca_paper", "status": item["status"],
              "fetched_at": item["fetched_at"], "errors": item["errors"],
              "account_suffix": str(account.get("account_number", account.get("id", "")))[-4:],
              "account_status": account.get("status"), "currency": account.get("currency"),
              "equity": number(account.get("equity")), "cash": number(account.get("cash")),
              "buying_power": number(account.get("buying_power")), "last_equity": number(account.get("last_equity")),
              "positions": None, "orders": None, "orders_scope": "最近最多 100 笔委托，不代表完整成交历史"}
    if "positions" in item:
        fields = ("symbol", "qty", "side", "avg_entry_price", "current_price", "market_value", "unrealized_pl", "unrealized_plpc")
        result["positions"] = [{k: p.get(k) for k in fields} for p in item["positions"]]
    if "orders" in item:
        fields = ("id", "client_order_id", "symbol", "side", "type", "qty", "notional", "status", "filled_qty", "filled_avg_price", "submitted_at", "filled_at")
        result["orders"] = [{k: p.get(k) for k in fields} for p in item["orders"]]
    return result


def model_portfolio(item, last_decision):
    if item["status"] != "connected":
        raise ValueError("Paper 账户快照不完整，无法生成可靠研究输入")
    account = item["account"]
    if account.get("status") != "ACTIVE":
        raise ValueError("Paper 账户不是 ACTIVE")
    cash = number(account.get("cash"))
    if cash is None or cash < 0:
        raise ValueError("账户现金无效或涉及融资，当前只支持现金做多研究")
    holdings = {}
    for position in item["positions"]:
        shares = number(position.get("qty"))
        price = number(position.get("current_price"))
        if shares is None or shares < 0 or price is None or price <= 0:
            raise ValueError("当前持仓不符合现金做多研究要求")
        if shares:
            holdings[position["symbol"]] = {"shares": shares, "last_mark": price}
    return {"cash": cash, "holdings": holdings, "last_decision": last_decision}
