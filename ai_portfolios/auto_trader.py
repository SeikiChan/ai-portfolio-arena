"""Daily Paper-only coordinator. Freezes one packet, then launches each isolated team once."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time as wait
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import execution, packet_broker
from .common import APP, ROOT, now, parse_time, read, stamp, write
from .credentials import paper_credentials
from .paper_accounts import account_identity
from .console_store import load_prompts

EASTERN = ZoneInfo("America/New_York")
RUN_AT = time(9, 35)  # five minutes into the regular session, not at the bell.
# Why not 09:30. Two things are at their worst in the first minutes: the opening auction's
# imbalance, which is priced into whatever limit we cross, and IEX quote staleness on the
# thinner names - and a quote older than MAX_QUOTE_AGE_SECONDS stops the entire plan, not
# just its own leg. Five minutes costs five minutes of drift and buys margin on both.
# The grace below still matters: a late or shortened open means the session may not be
# running yet even at 09:35, and a date once claimed as closed is never retried, so a
# clock that flips a moment late would silently swallow a whole trading day.
OPEN_GRACE_SECONDS = 600
ENGINES = ("claude_engine", "codex_engine")
STATE_ROOT = ROOT / "reports" / "auto_trader"
POLL_SECONDS = 30


def engine_timeout(engine_id, config):
    from engines.research import TIME_BUDGET, enabled
    return config["model_timeout_seconds"] + 180 + (TIME_BUDGET if enabled(engine_id, config) else 0)


def _shipped_mandates(mandates, providers):
    """Whether each engine ran the mandate it ships with, or one the console has since edited.

    Neither answer is wrong - the console exists so a mandate can change without a commit - but an
    edit to engines/<engine>/mandate.* on its own changes nothing about what runs here, and that
    silence is worth breaking in the one file somebody reads the morning after.
    """
    from engines import registry   # engines imports ai_portfolios; keep the dependency one-way

    verdict = {}
    for engine_id, provider in providers.items():
        shipped = getattr(registry.get(engine_id), "default_mandate", None) \
            if engine_id in registry.declared() else None
        try:
            verdict[engine_id] = shipped().strip() == mandates[provider].strip() if shipped else None
        except (OSError, ValueError):
            verdict[engine_id] = None
    return verdict


def _open_for_both():
    account_ids = {}
    for provider in ("claude", "gpt"):
        credentials = paper_credentials(only=provider)[provider]
        account_ids[provider] = account_identity(provider, credentials)
        execution.session_open(credentials)
    if account_ids["claude"] == account_ids["gpt"]:
        raise ValueError("Claude 与 Codex 绑定到了同一个 Alpaca Paper 账户；两队交易已停止")


def _seconds_until(moment):
    return (parse_time(moment) - now()).total_seconds() if moment else None


def _wait_for_open(grace=OPEN_GRACE_SECONDS):
    """None once both accounts see an open session, else the closed message.

    Waits only when the open is actually imminent, so a weekend still answers on the first check.
    """
    waited = 0
    while True:
        try:
            _open_for_both()
            return None
        except ValueError as exc:
            if "当前不在美股常规交易时段" not in str(exc):
                raise
            clock = execution.session_open(paper_credentials(only="claude")["claude"], require_open=False)
            remaining = _seconds_until(clock.get("next_open"))
            if remaining is None or waited >= grace or remaining > grace - waited:
                return str(exc)
            wait.sleep(min(POLL_SECONDS, max(1, remaining + 1)))
            waited += POLL_SECONDS


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
    closed = _wait_for_open()
    if closed:
        state = {"trading_date": trading_date, "status": "market_closed", "checked_at": stamp(),
                 "error": closed, "orders_submitted": False}
        write(state_file, state, exclusive=True)
        return state
    state = {"trading_date": trading_date, "status": "started", "started_at": stamp(),
             "schedule_time_et": RUN_AT.strftime("%H:%M"), "engines": {},
             "mandate_sha256": mandate_hashes, "mandate_matches_engine_default": _shipped_mandates(mandates, providers),
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

    def launch(engine_id):
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
            return {"exit_code": proc.returncode,
                "round_id": details.get("round_id"), "status": details.get("status"),
                "execution": exec_result,
                "execution_status": execution_status,
                "error": (proc.stderr or (proc.stdout if proc.returncode else ""))[-1000:] or None}
        except subprocess.TimeoutExpired:
            return {"status": "interrupted", "error": "daily engine process timed out"}
        except OSError as exc:
            return {"status": "failed", "error": type(exc).__name__}

    with ThreadPoolExecutor(max_workers=len(ENGINES)) as pool:
        pending = {pool.submit(launch, engine_id): engine_id for engine_id in ENGINES}
        for future in as_completed(pending):
            state["engines"][pending[future]] = future.result()
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
