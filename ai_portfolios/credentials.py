"""Shared credentials for every workspace. One place to rotate; values never leave this module.

Three key pairs exist and they are deliberately different accounts:

- the market-data pair, used only for GET market endpoints, shared by every team
- the Claude Paper account pair
- the GPT Paper account pair

Both teams must read the SAME market-data pair, because the packet they share has to come from one
source. The console used to override it with the GPT account's keys, which made the collector
asymmetric for no reason; the data returned is identical whichever paper key requests it, but the
asymmetry had to be read out of the code to be noticed, so it is gone.

Resolution order for every value: process environment, then the shared secrets directory, then a
workspace-local file (kept so an existing checkout keeps working). Nothing here prints or returns a
secret except the accessors themselves; `audit()` reports provenance and a short fingerprint so two
workspaces can prove they use the same key without either revealing it.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from .common import ROOT

SECRETS = Path(os.environ.get("AI_ARENA_SECRETS") or Path.home() / "ai-arena-secrets")
MARKET_DATA_FILE = "market-data.env"
PAPER_ACCOUNTS_FILE = "paper-accounts.env"
MARKET_KEYS = ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "FMP_API_KEY")
PAPER_PREFIX = {"claude": "Claude", "gpt": "Gpt"}
ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse(path, allowed=None):
    """Parse a dotenv file. Never executes it and never logs its contents."""
    path = Path(path)
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = ASSIGNMENT.match(line)
        if match:
            if allowed is not None and match[1] not in allowed:
                continue
            if match[1] in values:
                raise ValueError(f"{path.name} 存在重复变量名：{match[1]}")
            values[match[1]] = match[2].strip("\"'")
    return values


def fingerprint(value):
    """A short hash so two workspaces can confirm the same key without exposing it."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10] if value else None


def market_data_sources():
    return [SECRETS / MARKET_DATA_FILE, ROOT / ".env"]


def paper_account_sources():
    return [SECRETS / PAPER_ACCOUNTS_FILE, ROOT / ".env.aitrading"]


def _resolve(names, sources):
    found, origin = {}, {}
    # A team-specific account read must not materialize the other team's credentials in memory.
    files = [(path, parse(path, set(names))) for path in sources]
    for name in names:
        from_env = os.environ.get(name)
        if from_env:
            found[name], origin[name] = from_env, "environment"
            continue
        for path, values in files:
            if values.get(name):
                found[name], origin[name] = values[name], str(path)
                break
        else:
            found[name], origin[name] = None, "missing"
    return found, origin


def market_data_keys():
    """The shared GET-only market-data credentials. Identical for every team by construction."""
    values, _ = _resolve(MARKET_KEYS, market_data_sources())
    return values


def paper_credentials(only=None):
    """Paper account pairs. Pass only="gpt" to load one team's account and prove the other is untouched."""
    wanted = list(PAPER_PREFIX) if only is None else [only]
    if any(name not in PAPER_PREFIX for name in wanted):
        raise ValueError("未知的账户名")
    names = [PAPER_PREFIX[name] + suffix for name in wanted
             for suffix in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")]
    values, _ = _resolve(names, paper_account_sources())
    result = {}
    for name in wanted:
        key = values[PAPER_PREFIX[name] + "APCA_API_KEY_ID"]
        secret = values[PAPER_PREFIX[name] + "APCA_API_SECRET_KEY"]
        if not key or not secret:
            raise ValueError(f"{name} 的 Paper 凭据尚未完整配置；见 {SECRETS / PAPER_ACCOUNTS_FILE}")
        result[name] = (key, secret)
    if only is None and len({pair[0] for pair in result.values()}) != len(result):
        raise ValueError("两个模型使用了相同 API Key，请配置不同 Paper 账户")
    return result


def audit():
    """Provenance and fingerprints only. This output is safe to display and to commit to a report."""
    market, market_origin = _resolve(MARKET_KEYS, market_data_sources())
    names = [prefix + suffix for prefix in PAPER_PREFIX.values()
             for suffix in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")]
    accounts, account_origin = _resolve(names, paper_account_sources())
    rows = {}
    for name, value in {**market, **accounts}.items():
        if name.endswith("SECRET_KEY"):
            rows[name] = {"present": bool(value), "source": market_origin.get(name) or account_origin.get(name)}
        else:
            rows[name] = {"present": bool(value), "fingerprint": fingerprint(value),
                          "source": market_origin.get(name) or account_origin.get(name)}
    data_key = market["APCA_API_KEY_ID"]
    team_keys = {accounts["ClaudeAPCA_API_KEY_ID"], accounts["GptAPCA_API_KEY_ID"]} - {None}
    return {"secrets_directory": str(SECRETS), "credentials": rows,
            # A data key that is also a team's account key would make the collector asymmetric.
            "market_data_key_is_neutral": bool(data_key) and data_key not in team_keys,
            "classification": {"fingerprints": "derived", "secret_values": "never recorded"}}
