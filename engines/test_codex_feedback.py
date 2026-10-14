"""Offline checks for Codex's participation limits and process-feedback input."""
import json
from types import SimpleNamespace

import pytest

from ai_portfolios.common import write
from engines import codex_engine as codex


def config(**overrides):
    return {"universe": ["AAA", "BBB", "CCC"], "max_positions": 3, "max_weight": .3,
            "max_invested": .9, "min_invested": .5, "gpt_model": "fake",
            "model_timeout_seconds": 20, **overrides}


def packet():
    evidence = []
    for symbol in config()["universe"]:
        evidence.extend([
            {"id": "price:" + symbol, "kind": "derived_price", "symbol": symbol,
             "as_of": "2026-10-10T00:00:00Z", "data": {"last_close": 100}},
            {"id": "news:" + symbol, "kind": "news", "symbol": symbol,
             "data": {"text": "Observed company statement"}},
        ])
    return {"snapshot_id": "synthetic", "frozen_at": "2026-10-11T00:00:00Z",
            "features": [], "evidence": evidence, "errors": []}


def decision(weights=(.2, .2, .2)):
    rows = [{"symbol": symbol, "action": "new", "target_weight": weight,
             "horizon_days": 20, "confidence": .5, "thesis": "Synthetic hypothesis",
             "changes_vs_last": "Initial decision",
             "invalidation": {"kind": "time", "reference": "time:horizon_end",
                              "description": "Review at horizon"},
             "evidence_ids": ["price:" + symbol, "news:" + symbol],
             "fact_refs": ["price:" + symbol + "#last_close"], "evidence_quotes": []}
            for symbol, weight in zip(config()["universe"], weights)]
    return {"positions": rows, "overview": "Synthetic decision", "cash_reason": "Reserved cash",
            "evidence_gaps": [], "macro_claims": [], "memory_claims": [], "injection_flags": []}


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
