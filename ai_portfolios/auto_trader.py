"""Daily Paper-only coordinator. Freezes one packet, then launches each isolated team once."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time as wait
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import execution, packet_broker
from .common import APP, ROOT, read, stamp, write
from .credentials import paper_credentials
from .paper_accounts import account_identity
from .console_store import load_prompts

EASTERN = ZoneInfo("America/New_York")
RUN_AT = time(10, 0)  # assumption: wait 30 minutes after the regular-session open.
ENGINES = ("claude_engine", "codex_engine")
STATE_ROOT = ROOT / "reports" / "auto_trader"
POLL_SECONDS = 30


def engine_timeout(engine_id, config):
    from engines.research import TIME_BUDGET, enabled
    return config["model_timeout_seconds"] + 180 + (TIME_BUDGET if enabled(engine_id, config) else 0)


def _open_for_both():
    account_ids = {}
    for provider in ("claude", "gpt"):
        credentials = paper_credentials(only=provider)[provider]
        account_ids[provider] = account_identity(provider, credentials)
        execution.session_open(credentials)
    if account_ids["claude"] == account_ids["gpt"]:
        raise ValueError("Claude 与 Codex 绑定到了同一个 Alpaca Paper 账户；两队交易已停止")


def run_daily(trading_date=None):
    """Run once per Eastern trading date; a claimed date is never retried automatically."""
    trading_date = trading_date or __import__("datetime").datetime.now(EASTERN).date().isoformat()
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    state_file = STATE_ROOT / f"{trading_date}.json"
    if state_file.exists():
        return read(state_file)

    mandates = load_prompts()
    providers = {"claude_engine": "claude", "codex_engine": "gpt"}
    mandate_hashes = {engine_id: hashlib.sha256(mandates[provider].encode("utf-8")).hexdigest()
                      for engine_id, provider in providers.items()}

    # Avoid collecting data on weekends/holidays. Alpaca's clock is the source of truth.
    try:
        _open_for_both()
    except ValueError as exc:
        if "当前不在美股常规交易时段" not in str(exc):
            raise
        state = {"trading_date": trading_date, "status": "market_closed", "checked_at": stamp(),
                 "error": str(exc), "orders_submitted": False}
        write(state_file, state, exclusive=True)
        return state
    state = {"trading_date": trading_date, "status": "started", "started_at": stamp(),
             "schedule_time_et": RUN_AT.strftime("%H:%M"), "engines": {},
             "mandate_sha256": mandate_hashes,
             "classification": "assumption: one automatic Paper trading cycle per session"}
    write(state_file, state, exclusive=True)
    try:
        pointer = packet_broker.freeze(read(APP / "config.json"), collector="daily_auto_trader")
    except Exception as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:250]}", completed_at=stamp())
        write(state_file, state)
        return state
    state.update(packet_id=pointer["packet_id"], packet_sha256=pointer["packet_sha256"], packet_frozen_at=pointer["frozen_at"])
    write(state_file, state)

    for engine_id in ENGINES:
        provider = providers[engine_id]
        mandate_directory = STATE_ROOT / "mandates" / trading_date
        mandate_directory.mkdir(parents=True, exist_ok=True)
        mandate_file = mandate_directory / f"{engine_id}.txt"
        mandate_file.write_text(mandates[provider], encoding="utf-8")
        command = [sys.executable, "-m", "engines.run", "--engine", engine_id,
                   "--packet-id", pointer["packet_id"], "--mandate-file", str(mandate_file)]
        try:
            proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=engine_timeout(engine_id, read(APP / "config.json")))
            details = {}
            try:
                details = json.loads(proc.stdout)
            except ValueError:
                pass
            exec_result = details.get("execution") or {}
            outcomes = {item.get("outcome") for item in exec_result.get("results", [])}
            if exec_result.get("execution_error"):
                execution_status = "failed"
            elif "submission_unknown" in outcomes:
                execution_status = "submission_unknown"
            elif "rejected" in outcomes:
                execution_status = "partial_failure"
            else:
                execution_status = exec_result.get("mode", "unknown")
            state["engines"][engine_id] = {"exit_code": proc.returncode,
                "round_id": details.get("round_id"), "status": details.get("status"),
                "execution": exec_result,
                "execution_status": execution_status,
                "error": (proc.stderr or (proc.stdout if proc.returncode else ""))[-1000:] or None}
        except subprocess.TimeoutExpired:
            state["engines"][engine_id] = {"status": "interrupted", "error": "daily engine process timed out"}
        state["updated_at"] = stamp()
        write(state_file, state)

    successes = sum(row.get("status") == "completed" and row.get("execution_status") not in
                    {"failed", "submission_unknown", "partial_failure"}
                    for row in state["engines"].values())
    state["status"] = "completed" if successes == len(ENGINES) else "partial_failure" if successes else "failed"
    state["completed_at"] = stamp()
    write(state_file, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="check once; intended for a scheduled daily task")
    args = parser.parse_args()
    if args.once:
        current = datetime.now(EASTERN)
        if current.time() < RUN_AT:
            seconds = (datetime.combine(current.date(), RUN_AT, tzinfo=EASTERN) - current).total_seconds()
            wait.sleep(max(0, seconds))
            current = datetime.now(EASTERN)
        try:
            state = run_daily(current.date().isoformat())
        except Exception as exc:
            print(json.dumps({"status": "not_started", "error": f"{type(exc).__name__}: {str(exc)[:250]}"}, ensure_ascii=False))
            raise SystemExit(1)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return

    print(f"Paper auto trader waiting for {RUN_AT.strftime('%H:%M')} America/New_York each trading day", flush=True)
    while True:
        current = datetime.now(EASTERN)
        date_state = STATE_ROOT / f"{current.date().isoformat()}.json"
        if current.weekday() < 5 and current.time() >= RUN_AT and not date_state.exists():
            try:
                state = run_daily(current.date().isoformat())
                print(json.dumps({"date": state["trading_date"], "status": state["status"]}, ensure_ascii=False), flush=True)
            except Exception as exc:
                print(f"Not started: {type(exc).__name__}: {str(exc)[:250]}", flush=True)
        wait.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
