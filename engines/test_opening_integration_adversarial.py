"""Independent offline checks of the opening coordinator and Codex acceptance boundary."""
from datetime import time
import json
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ai_portfolios import auto_trader
from ai_portfolios.common import write, read, digest
from engines import codex_engine as codex


def config(**overrides):
    return {"universe": ["AAA", "BBB", "CCC"], "max_positions": 3, "max_weight": .3,
            "max_invested": .9, "min_invested": .5, "gpt_model": "fake",
            "model_timeout_seconds": 20, **overrides}


def packet():
    evidence = []
    for symbol in config()["universe"]:
        evidence.extend([{"id": "price:" + symbol, "kind": "derived_price", "symbol": symbol,
            "as_of": "2026-10-10T00:00:00Z", "data": {"last_close": 100}},
            {"id": "news:" + symbol, "kind": "news", "symbol": symbol, "data": {"text": "Observed company statement"}}])
    return {"snapshot_id": "synthetic", "frozen_at": "2026-10-11T00:00:00Z", "features": [], "evidence": evidence, "errors": []}


def decision(weights=(.2, .2, .2)):
    rows = [{"symbol": symbol, "action": "new", "target_weight": weight,
             "horizon_days": 20, "confidence": .5, "thesis": "Synthetic hypothesis", "changes_vs_last": "Initial decision",
             "invalidation": {"kind": "time", "reference": "time:horizon_end", "description": "Review at horizon"},
             "evidence_ids": ["price:" + symbol, "news:" + symbol], "fact_refs": ["price:" + symbol + "#last_close"],
             "evidence_quotes": []} for symbol, weight in zip(config()["universe"], weights)]
    return {"positions": rows, "overview": "Synthetic decision", "cash_reason": "Reserved cash", "evidence_gaps": [],
            "macro_claims": [], "memory_claims": [], "injection_flags": []}


@pytest.mark.parametrize("weights", [(), (.01,), (.2, .2), (.2, .2, .099)])
def test_codex_rejects_below_floor(weights):
    with pytest.raises(ValueError):
        codex.validate(decision(weights), config(), packet(), {"holdings": {}})


@pytest.mark.parametrize("weights", [(.3, .2), (.3, .3, .3)])
def test_codex_accepts_exact_limits(weights):
    assert codex.validate(decision(weights), config(), packet(), {"holdings": {}})


@pytest.mark.parametrize("overrides", [{"min_invested": True}, {"min_invested": float("nan")},
    {"min_invested": -.1}, {"min_invested": 1}, {"max_weight": .1}, {"max_positions": 1},
    {"max_positions": True}, {"max_invested": float("nan")}])
def test_codex_rejects_invalid_or_impossible_limits(overrides):
    with pytest.raises(ValueError):
        codex.validate(decision(), config(**overrides), packet(), {"holdings": {}})


def setup_daily(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_trader, "STATE_ROOT", tmp_path / "state")
    monkeypatch.setattr(auto_trader, "APP", tmp_path)
    write(tmp_path / "config.json", config())
    monkeypatch.setattr(auto_trader, "_open_for_both", lambda: None)
    monkeypatch.setattr(auto_trader, "load_prompts", lambda: {"claude": "claude-only", "gpt": "gpt-only"})
    freezer = Mock(return_value={"packet_id": "same-frozen", "packet_sha256": "a" * 64, "frozen_at": "2026-10-12T13:30:00Z"})
    monkeypatch.setattr(auto_trader.packet_broker, "freeze", freezer)
    return freezer


def result(status="completed"):
    return SimpleNamespace(returncode=0, stdout=json.dumps({"status": status, "round_id": "fake-round",
        "execution": {"mode": "paper", "results": []}}), stderr="")


def test_schedule_waits_out_the_opening_minutes():
    """Inside the session, deliberately not at the bell: the opening auction's imbalance and the
    worst IEX quote staleness both land in the first minutes, and one stale quote stops the whole
    plan rather than its own leg."""
    assert auto_trader.RUN_AT == time(9, 35)
    assert auto_trader.RUN_AT > time(9, 30)


def test_both_engines_start_concurrently_with_same_packet(tmp_path, monkeypatch):
    freezer = setup_daily(tmp_path, monkeypatch)
    barrier = threading.Barrier(2)
    started = []
    def run(command, **kwargs):
        engine = command[command.index("--engine") + 1]
        assert command[command.index("--packet-id") + 1] == "same-frozen"
        mandate = Path(command[command.index("--mandate-file") + 1]).read_text(encoding="utf-8")
        assert mandate == ("claude-only" if engine == "claude_engine" else "gpt-only")
        started.append(engine)
        barrier.wait(timeout=3)
        return result()
    monkeypatch.setattr(auto_trader.subprocess, "run", run)
    state = auto_trader.run_daily("2026-10-12")
    assert set(started) == set(auto_trader.ENGINES)
    assert state["status"] == "completed"
    assert state["schedule_time_et"] == "09:35"
    freezer.assert_called_once()


def test_engine_timeout_does_not_skip_other_team(tmp_path, monkeypatch):
    setup_daily(tmp_path, monkeypatch)
    def run(command, **kwargs):
        if command[command.index("--engine") + 1] == "codex_engine":
            raise subprocess.TimeoutExpired(command, 1)
        return result()
    monkeypatch.setattr(auto_trader.subprocess, "run", run)
    state = auto_trader.run_daily("2026-10-12")
    assert state["status"] == "partial_failure"
    assert state["engines"]["claude_engine"]["status"] == "completed"
    assert state["engines"]["codex_engine"]["status"] == "interrupted"


def test_already_claimed_day_never_relaunches(tmp_path, monkeypatch):
    freezer = setup_daily(tmp_path, monkeypatch)
    launch = Mock(return_value=result())
    monkeypatch.setattr(auto_trader.subprocess, "run", launch)
    first = auto_trader.run_daily("2026-10-12")
    assert auto_trader.run_daily("2026-10-12") == first
    assert launch.call_count == 2
    freezer.assert_called_once()


def test_decide_receives_sealed_feedback_and_floor(tmp_path, monkeypatch):
    feedback = {"records": [{"round_id": "prior", "reward": -1}], "investment_skill": "unknown"}
    write(tmp_path / "feedback_input.json", feedback)
    monkeypatch.setattr(codex, "_command", lambda *args: ["fake-cli"])
    def launch(command, **kwargs):
        context = json.loads(kwargs["input"].split("\n\n输入：\n", 1)[1])
        assert context["limits"]["min_invested"] == .5
        assert context["process_feedback"] == feedback
        return SimpleNamespace(returncode=1, stdout="", stderr="service unavailable")
    monkeypatch.setattr(codex.subprocess, "run", launch)
    status = codex.ENGINE.decide("gpt", packet(), {"holdings": {}}, "mandate", tmp_path, config())
    assert status["status"] == "failed"
    assert status["failure_kind"] != "invalid_output"
