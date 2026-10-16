"""Offline checks for Codex's participation limits and process-feedback input."""
import json
from types import SimpleNamespace

import pytest

from ai_portfolios.common import write
from engines import codex_engine as codex, research


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


def test_codex_can_add_to_existing_paper_holding_without_prior_target_weight():
    plan = decision((.25, .15, .15))
    plan["positions"][0]["action"] = "add"
    previous = {"cash": 800, "holdings": {"AAA": {"shares": 2, "last_mark": 100}}, "last_decision": None}

    assert codex.validate(plan, config(), packet(), previous)


def test_codex_rejects_add_that_does_not_exceed_actual_starting_weight():
    plan = decision((.20, .15, .15))
    plan["positions"][0]["action"] = "add"
    previous = {"cash": 800, "holdings": {"AAA": {"shares": 2, "last_mark": 100}}, "last_decision": None}

    with pytest.raises(ValueError, match="action conflicts with prior"):
        codex.validate(plan, config(), packet(), previous)


def test_codex_does_not_call_an_existing_holding_new():
    previous = {"cash": 800, "holdings": {"AAA": {"shares": 2, "last_mark": 100}}, "last_decision": None}

    with pytest.raises(ValueError, match="action conflicts with prior"):
        codex.validate(decision(), config(), packet(), previous)


def test_codex_can_trim_existing_paper_holding_without_prior_target_weight():
    plan = decision((.15, .20, .20))
    plan["positions"][0]["action"] = "trim"
    previous = {"cash": 800, "holdings": {"AAA": {"shares": 2, "last_mark": 100}}, "last_decision": None}

    assert codex.validate(plan, config(), packet(), previous)


def test_codex_hold_must_match_the_first_round_marked_weight():
    plan = decision((.002, .25, .25))
    plan["positions"][0]["action"] = "hold"
    previous = {"cash": 99800, "holdings": {"AAA": {"shares": 2, "last_mark": 100}}, "last_decision": None}

    assert codex.validate(plan, config(), packet(), previous)
    plan["positions"][0]["target_weight"] = .01
    with pytest.raises(ValueError, match="action conflicts with prior target weight"):
        codex.validate(plan, config(), packet(), previous)


def test_codex_uses_same_experiment_target_weight_when_available():
    plan = decision((.18, .20, .20))
    plan["positions"][0]["action"] = "add"
    previous = {"cash": 850, "holdings": {"AAA": {"shares": 3, "last_mark": 50}},
                "last_decision": {"positions": [{"symbol": "AAA", "target_weight": .20}]}}

    with pytest.raises(ValueError, match="action conflicts with prior"):
        codex.validate(plan, config(), packet(), previous)

    plan["positions"][0]["target_weight"] = .22
    assert codex.validate(plan, config(), packet(), previous)


def test_codex_requires_complete_account_data_to_derive_first_round_baseline():
    previous = {"holdings": {"AAA": {"shares": 2, "last_mark": 100}}, "last_decision": None}

    with pytest.raises(ValueError, match="valid account cash"):
        codex.validate(decision((.25, .15, .15)), config(), packet(), previous)


def test_holdings_baseline_change_starts_a_new_experiment_identity():
    old_engine = SimpleNamespace(engine_id="codex_engine", version="3.0.0-opening-participation-feedback",
                                 instruction=codex.ENGINE.instruction)

    assert research.identity(codex.ENGINE, config(), "mandate") != research.identity(old_engine, config(), "mandate")


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
