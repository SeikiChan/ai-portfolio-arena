import json
from datetime import datetime, timedelta, timezone

import pytest

from ai_portfolios import auto_trader
from ai_portfolios.common import read, write

NOW = datetime(2026, 10, 12, 13, 30, tzinfo=timezone.utc)  # 09:30 America/New_York


def clock(open_in_seconds=None, is_open=False):
    moment = NOW + timedelta(seconds=open_in_seconds) if open_in_seconds is not None else None
    return {"is_open": is_open, "next_open": moment.isoformat() if moment else None}


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_trader, "STATE_ROOT", tmp_path / "auto_trader")
    monkeypatch.setattr(auto_trader, "now", lambda: NOW)
    monkeypatch.setattr(auto_trader, "paper_credentials", lambda only=None: {only: ("k", "s")})
    monkeypatch.setattr(auto_trader.wait, "sleep", lambda seconds: None)
    return tmp_path


def test_a_clock_that_flips_a_moment_late_does_not_cost_the_whole_day(quiet, monkeypatch):
    """09:30 is a boundary. A date claimed as closed is never retried, so a one-second race
    between the task starting and the broker clock flipping would swallow a trading day."""
    calls = []

    def opening_soon(credentials):
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("当前不在美股常规交易时段（下次开市 soon），不提交委托")

    monkeypatch.setattr(auto_trader, "_open_for_both", opening_soon)
    monkeypatch.setattr(auto_trader.execution, "session_open",
                        lambda credentials, getter=None, require_open=True: clock(open_in_seconds=20))
    monkeypatch.setattr(auto_trader, "_open_for_both", lambda: opening_soon(None))
    assert auto_trader._wait_for_open() is None
    assert len(calls) == 3


def test_a_weekend_does_not_wait_around(quiet, monkeypatch):
    checks = []

    def closed():
        checks.append(1)
        raise ValueError("当前不在美股常规交易时段（下次开市 Monday），不提交委托")

    monkeypatch.setattr(auto_trader, "_open_for_both", closed)
    # Next open is two days out, so there is nothing to wait across: answer on the first check.
    monkeypatch.setattr(auto_trader.execution, "session_open",
                        lambda credentials, getter=None, require_open=True: clock(open_in_seconds=172800))
    assert "常规交易时段" in auto_trader._wait_for_open()
    assert len(checks) == 1


def test_a_closed_market_is_recorded_and_submits_nothing(quiet, monkeypatch):
    monkeypatch.setattr(auto_trader, "_wait_for_open", lambda *a, **k: "当前不在美股常规交易时段（下次开市 x）")
    state = auto_trader.run_daily("2026-10-11")
    assert state["status"] == "market_closed" and state["orders_submitted"] is False
    assert read(auto_trader.STATE_ROOT / "2026-10-11.json")["status"] == "market_closed"


def test_a_claimed_trading_date_is_never_run_twice(quiet, monkeypatch):
    attempts = []
    monkeypatch.setattr(auto_trader, "_wait_for_open", lambda *a, **k: attempts.append(1) or "closed")
    auto_trader.run_daily("2026-10-12")
    auto_trader.run_daily("2026-10-12")
    # The second call must return the stored state without touching the broker again.
    assert len(attempts) == 1


def test_an_unexpected_broker_error_is_not_swallowed_as_closed(quiet, monkeypatch):
    def broken():
        raise ValueError("无法读取交易时段：HTTP 500")

    monkeypatch.setattr(auto_trader, "_open_for_both", broken)
    with pytest.raises(ValueError, match="HTTP 500"):
        auto_trader._wait_for_open()


def test_one_engine_failing_does_not_stop_the_other(quiet, monkeypatch):
    monkeypatch.setattr(auto_trader, "_wait_for_open", lambda *a, **k: None)
    monkeypatch.setattr(auto_trader.packet_broker, "freeze", lambda *a, **k: {
        "packet_id": "p1", "packet_sha256": "h", "frozen_at": NOW.isoformat()})

    class Result:
        def __init__(self, code, out):
            self.returncode, self.stdout, self.stderr = code, out, ""

    def run(command, **kwargs):
        engine = command[command.index("--engine") + 1]
        if engine == "claude_engine":
            return Result(1, json.dumps({"status": "failed", "error": "boom"}))
        return Result(0, json.dumps({"status": "completed", "round_id": "r2"}))

    monkeypatch.setattr(auto_trader.subprocess, "run", run)
    state = auto_trader.run_daily("2026-10-13")
    # A failure on one account must never suppress the other account's decision.
    assert state["status"] == "partial_failure"
    assert set(state["engines"]) == {"claude_engine", "codex_engine"}


def test_the_state_file_is_never_silently_overwritten(quiet, monkeypatch):
    monkeypatch.setattr(auto_trader, "_wait_for_open", lambda *a, **k: "closed")
    auto_trader.run_daily("2026-10-14")
    path = auto_trader.STATE_ROOT / "2026-10-14.json"
    with pytest.raises(FileExistsError):
        write(path, {"status": "tampered"}, exclusive=True)
