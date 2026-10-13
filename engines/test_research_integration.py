"""Offline orchestration tests: a failed research stage must never reach execution."""
from contextlib import nullcontext
from copy import deepcopy
import json

import pytest

from ai_portfolios import execution
from ai_portfolios.common import digest, read, stamp, write
from engines import contract, publish, research, run
from engines.test_contract import round_dir


@pytest.mark.parametrize("provider", ["claude", "gpt"])
@pytest.mark.parametrize("fail", [False, True])
def test_round_uses_research_input_and_seals_failures(tmp_path, monkeypatch, provider, fail):
    engine_id = {"claude": "claude_engine", "gpt": "codex_engine"}[provider]
    config = {"research": {"enabled": True}, "universe": ["MSFT"], "model_timeout_seconds": 60}
    packet = {"snapshot_id": "base", "frozen_at": stamp(), "features": [], "evidence": [], "errors": []}
    calls = []

    class Engine:
        version, author = "test", "test"

        def instruction(self, mode):
            return "Fixed test instruction"

        def decide(self, got_provider, got_packet, previous, mandate, directory, config):
            assert got_provider == provider
            assert got_packet["research"]["provider"] == provider
            assert got_packet["evidence"][0]["id"] == f"research:{provider}:1"
            assert previous["last_decision"] is None
            (directory / f"{provider}_prompt.txt").write_text("Decision INPUT", encoding="utf-8")
            calls.append("decide")
            return {"status": "validated", "decision": {"positions": []}}

    engine = Engine()
    engine.engine_id = engine_id
    monkeypatch.setattr(run.registry, "get", lambda _: engine)
    monkeypatch.setattr(run, "_engine_lock", lambda _: nullcontext())
    monkeypatch.setattr(run, "_bind_account", lambda *args: None)
    monkeypatch.setattr(run, "paper_credentials", lambda only: {only: {}})

    def claim(config, staged, **kwargs):
        write(staged / "packet.json", packet)
        return {"packet_id": "base", "packet_sha256": digest(staged / "packet.json"),
                "frozen_at": packet["frozen_at"], "age_seconds": 0}

    def account(got_provider, credentials, path):
        assert got_provider == provider
        value = {"status": "connected", "fetched_at": stamp(), "positions": [],
                 "account": {"id": "test-account", "status": "ACTIVE", "cash": "1000"}}
        write(path / "snapshot.json", value)
        return value

    monkeypatch.setattr(run.packet_broker, "claim", claim)
    monkeypatch.setattr(run, "read_one", account)
    monkeypatch.setattr(publish, "build", lambda *args: {})
    monkeypatch.setitem(publish.DEFAULT_EXPORTS, engine_id, tmp_path / "export.json")

    def execute(*args, **kwargs):
        assert kwargs["submit_orders"] is False
        calls.append("execute")
        return {"orders_submitted": False}

    monkeypatch.setattr(execution, "execute", execute)

    def planner(*args):
        if fail:
            raise RuntimeError("Planner failed")
        step = args[4]
        return {"done": step > 0, "actions": [] if step else [{"tool": "calculate", "query": "",
                "url": "", "symbol": "MSFT", "start_page": 1, "expression": "1+1"}]}

    monkeypatch.setattr(research, "invoke", planner)
    monkeypatch.setattr(research, "dispatch", lambda *args: {"status": "ok", "classification": "derived", "text": "2"})
    directory, job, _ = run.run(engine_id, "test", config=config, output_root=tmp_path / "rounds", submit_orders=False)
    assert read(directory / "packet.json") == packet
    assert contract.validate_round(directory)["status"] == ("failed" if fail else "completed")
    assert calls == ([] if fail else ["decide", "execute"])
    assert job["experiment_id"].startswith("public-research-v1-")
    if not fail:
        assert job["decision_packet_sha256"] == digest(directory / "decision_packet.json")
        receipt = read(directory / "receipt.json")
        assert "research_manifest.json" in receipt["files"]
        assert "decision_packet.json" in receipt["files"]
        write(directory / "research_01_result.json", {"tampered": True})
        with pytest.raises(ValueError, match="hash mismatch"):
            contract.validate_round(directory)


def test_previous_decision_never_crosses_explicit_experiment(tmp_path):
    for name, experiment in [("01", "old"), ("02", "new")]:
        write(tmp_path / name / "round.json", {"experiment_id": experiment})
        write(tmp_path / name / "gpt_status.json", {"status": "validated", "decision": {"experiment": experiment}})
    assert run._latest_decision(tmp_path, "gpt", "old") == {"experiment": "old"}
    assert run._latest_decision(tmp_path, "gpt", "packet-only-v1-abc") is None


def test_disable_research_creates_distinct_experiment():
    engine = run.registry.get("codex_engine")
    config = {"research": {"enabled": True}}
    enabled = research.identity(engine, config, "mandate")
    config["research"]["enabled"] = False
    disabled = research.identity(engine, config, "mandate")
    assert disabled != enabled and disabled.startswith("packet-only-v1-")


def test_each_engine_ships_its_own_mandate_and_neither_leaks_into_the_other():
    """Both engines now use run.py's default_mandate hook, so the shared fallback reaches neither.

    claude_engine used to fall through to run.DEFAULT_MANDATE because it declared nothing; it now
    ships engines/claude_engine/mandate.md. What still has to hold is independence: one team's
    mandate must never become the other's, and the shared fallback must not silently stand in for
    either, which is what made this assertion worth keeping.
    """
    codex = run.default_mandate("codex_engine")
    claude = run.default_mandate("claude_engine")
    assert codex.startswith("委托版本：codex-capital-growth-v2-20261011")
    assert claude != codex
    assert run.DEFAULT_MANDATE not in (codex, claude)
    engine = run.registry.get("codex_engine")
    assert research.identity(engine, {}, codex) != research.identity(engine, {}, run.DEFAULT_MANDATE)


def test_research_is_enabled_only_for_explicitly_selected_team():
    config = {"research": {"enabled": True, "engines": ["codex_engine"]}}
    assert research.enabled("codex_engine", config)
    assert not research.enabled("claude_engine", config)


def test_coordinator_timeout_includes_research_budget_only_for_enabled_engine():
    from ai_portfolios.auto_trader import engine_timeout
    config = {"model_timeout_seconds": 600, "research": {"enabled": True, "engines": ["codex_engine"]}}
    assert engine_timeout("codex_engine", config) == 780 + research.TIME_BUDGET
    assert engine_timeout("claude_engine", config) == 780


def test_unreadable_source_bytes_survive_parse_failure(monkeypatch):
    from engines import research_tools
    import base64
    source = {"url": "https://example.org/broken.pdf", "content_type": "application/pdf", "body": b"%PDF-broken"}
    monkeypatch.setattr(research_tools, "fetch_url", lambda _: source)
    result = research_tools.execute_tool({"tool": "read_url", "url": source["url"]})
    assert result["status"] == "unknown"
    assert base64.b64decode(result["raw_base64"]) == source["body"]


def test_codex_export_does_not_expose_private_prompt_input(tmp_path):
    directory = round_dir(tmp_path, prompt_body='Rules\n输入：\n{"cash": "private-value"}',
                          engine_id="codex_engine", providers=["gpt"], mandate_sha256={"gpt": "b" * 64},
                          account_snapshot={"provider": "gpt", "account_id_sha256": "c" * 64, "files": {}})
    text, _, _ = publish.prompt_text(directory, "gpt")
    assert "private-value" not in text and "Rules" in text


def test_crossref_metadata_is_not_promoted_to_full_text(monkeypatch):
    from engines import research_tools
    monkeypatch.setattr(research_tools, "fetch_url", lambda url: {"url": url, "content_type": "application/json",
        "body": json.dumps({"message": {"items": [{"title": ["Example"], "DOI": "10/example"}]}}).encode()})
    result = research_tools.execute_tool({"tool": "search_scholarly", "query": "portfolio"})
    assert result["status"] == "ok" and result["result_count"] == 1
    assert "not full text" in result["text"]


def test_utf16_xml_entity_declaration_rejected():
    from engines.research_tools import _feed
    body = '<!DOCTYPE rss [<!ENTITY x "bad">]><rss><channel>&x;</channel></rss>'.encode("utf-16")
    with pytest.raises(ValueError):
        _feed(body)
