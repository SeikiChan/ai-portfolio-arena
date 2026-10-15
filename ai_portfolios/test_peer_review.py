import json
import threading

import requests

import pytest

from ai_portfolios import peer_review


PACKET = "a" * 64


def post(provider, team_id, round_id):
    return {
        "id": round_id + ":" + provider,
        "round_id": round_id,
        "provider": provider,
        "engine_id": "claude_engine" if provider == "claude" else "codex_engine",
        "status": "validated",
        "packet_sha256": PACKET,
        "completed_at": "2026-10-15T16:00:00Z",
        "overview": "收入证据支持持有，但估值和现金流仍未知。",
        "cash_reason": "保留现金等待后续现金流披露。",
        "decision_details": [{
            "symbol": "MSFT", "action": "hold", "target_weight": 0.1,
            "thesis": "营业利润改善支持继续持有，现金流尚未确认。",
            "changes_vs_last": "维持上轮目标。", "invalidation": {"kind": "price_below", "reference": "500"},
            "horizon_days": 14, "confidence": 0.6,
            "evidence_ids": ["price:MSFT", "income:MSFT:0"],
            "facts": [{"ref": "income:MSFT:0#operatingIncome", "value": 123}],
        }],
        "decision_evidence": [{"id": "income:MSFT:0", "kind": "financials", "as_of": "2026-10-01",
                               "headline": "Quarterly results", "url": "https://example.test/filing"}],
        "account_observed": {"positions": [{"symbol": "SECRET"}], "cash": 123},
        "broker_orders": [{"request": {"symbol": "SECRET"}}],
        "full_prompt": "private prompt", "research_trace": [{"text": "private research"}],
    }


def write_exports(tmp_path, claude_post=None, gpt_post=None):
    paths = {}
    for team_id, value in (("claude-independent", claude_post), ("gpt-independent", gpt_post)):
        path = tmp_path / f"{team_id}.json"
        path.write_text(json.dumps({"schema_version": 1, "team_id": team_id,
                                    "rounds": [value] if value else []}), encoding="utf-8")
        paths[team_id] = path
    return paths


def output(body="具体的说法值得讨论，因为现有公开内容没有给出自由现金流、资本开支和估值。"):
    return {"stance": "mixed", "body": body, "focus_symbols": ["MSFT"]}


def test_matching_posts_requires_same_packet_and_one_validated_post_per_team(tmp_path):
    claude = post("claude", "claude-independent", "round-c")
    gpt = post("gpt", "gpt-independent", "round-g")
    paths = write_exports(tmp_path, claude, gpt)
    assert set(peer_review.matching_posts(PACKET, paths)) == {"claude", "gpt"}

    gpt["packet_sha256"] = "b" * 64
    paths = write_exports(tmp_path, claude, gpt)
    assert peer_review.matching_posts(PACKET, paths) is None

    gpt["packet_sha256"] = PACKET
    gpt["status"] = "failed"
    paths = write_exports(tmp_path, claude, gpt)
    assert peer_review.matching_posts(PACKET, paths) is None


def test_review_input_is_an_allowlist_and_does_not_include_account_or_private_engine_data():
    clean = peer_review.public_post(post("claude", "claude-independent", "round-c"))
    serialized = json.dumps(clean, ensure_ascii=False)
    assert "account_observed" not in serialized
    assert "broker_orders" not in serialized
    assert "private prompt" not in serialized
    assert "private research" not in serialized
    assert clean["positions"][0]["facts"][0]["value"] == 123
    assert clean["cited_evidence_headlines"][0]["headline"] == "Quarterly results"


def test_review_output_validation_rejects_invalid_fields_and_empty_body():
    assert peer_review._validate_output(output())["stance"] == "mixed"
    with pytest.raises(ValueError, match="fields"):
        peer_review._validate_output({**output(), "order": "buy"})
    with pytest.raises(ValueError, match="length"):
        peer_review._validate_output(output("短评"))
    with pytest.raises(ValueError, match="symbols"):
        peer_review._validate_output({**output(), "focus_symbols": ["msft"]})
    with pytest.raises(ValueError, match="published post"):
        peer_review._validate_output(output(), {"AAPL"})


def test_run_for_packet_creates_two_immutable_reviews_and_read_discussions_returns_them(tmp_path):
    claude = post("claude", "claude-independent", "round-c")
    gpt = post("gpt", "gpt-independent", "round-g")
    exports = write_exports(tmp_path, claude, gpt)
    invoked = []

    def invoke(provider, prompt, config):
        invoked.append((provider, prompt))
        return output(), {"model": config[provider + "_model"], "usage": {"input_tokens": 10},
                          "raw_response": json.dumps(output(), ensure_ascii=False)}

    result = peer_review.run_for_packet(PACKET, config={"claude_model": "claude-test", "gpt_model": "gpt-test"},
                                        exports=exports, output_root=tmp_path / "reviews", invoke=invoke)
    assert result["status"] == "completed"
    assert {provider for provider, _ in invoked} == {"claude", "gpt"}
    assert all("account_observed" not in prompt and "private prompt" not in prompt for _, prompt in invoked)
    rows = peer_review.read_discussions(tmp_path / "reviews")
    assert len(rows) == 2
    assert {(row["parent_post_id"], row["reviewer_provider"]) for row in rows} == {
        ("round-c:claude", "gpt"), ("round-g:gpt", "claude")}
    assert all(row["parent_reply_id"] is None for row in rows)
    assert result["status"] == "completed"


def test_failed_review_does_not_block_other_reply_or_invent_comment(tmp_path):
    exports = write_exports(tmp_path, post("claude", "claude-independent", "round-c"),
                             post("gpt", "gpt-independent", "round-g"))

    def invoke(provider, prompt, config):
        if provider == "claude":
            raise RuntimeError("model unavailable")
        return output(), {"model": "gpt-test", "raw_response": json.dumps(output(), ensure_ascii=False)}

    result = peer_review.run_for_packet(PACKET, config={"claude_model": "claude-test", "gpt_model": "gpt-test"},
                                        exports=exports, output_root=tmp_path / "reviews", invoke=invoke)
    assert result["status"] == "partial_failure"
    rows = peer_review.read_discussions(tmp_path / "reviews")
    assert len(rows) == 1
    assert rows[0]["reviewer_provider"] == "gpt"


def test_review_commands_disable_tools_and_untrusted_environment_is_filtered(monkeypatch):
    monkeypatch.setattr(peer_review, "executable", lambda name: f"{name}.exe")
    claude = peer_review._command("claude", {"claude_model": "sonnet"}, "schema", "answer")
    codex = peer_review._command("gpt", {"gpt_model": "gpt-test"}, "schema", "answer")
    assert "--tools" in claude and claude[claude.index("--tools") + 1] == ""
    assert not any("web" in value and "disabled" not in value for value in claude)
    assert "--sandbox" in codex and codex[codex.index("--sandbox") + 1] == "read-only"
    assert 'web_search="disabled"' in codex
    monkeypatch.setenv("APCA_API_KEY_ID", "should-not-pass")
    assert "APCA_API_KEY_ID" not in peer_review.model_environment()


def test_missing_or_mismatched_pair_skips_without_model_calls(tmp_path):
    exports = write_exports(tmp_path, post("claude", "claude-independent", "round-c"), None)

    def forbidden(*args, **kwargs):
        raise AssertionError("review model must not run without a matched pair")

    result = peer_review.run_for_packet(PACKET, config={"claude_model": "claude-test", "gpt_model": "gpt-test"},
                                        exports=exports, output_root=tmp_path / "reviews", invoke=forbidden)
    assert result["status"] == "skipped"
    assert not (tmp_path / "reviews").exists()


def test_run_after_trading_waits_for_both_and_keeps_review_failure_separate():
    calls = []
    runner = lambda packet, config: calls.append((packet, config)) or {"status": "completed"}
    partial = {"claude_engine": {"status": "completed"}, "codex_engine": {"status": "failed"}}
    skipped = peer_review.run_after_trading(partial, PACKET, config={"review": True}, runner=runner)
    assert skipped["status"] == "skipped" and calls == []

    completed = {"claude_engine": {"status": "completed"}, "codex_engine": {"status": "completed"}}
    result = peer_review.run_after_trading(completed, PACKET, config={"review": True}, runner=runner)
    assert result["status"] == "completed"
    assert calls == [(PACKET, {"review": True})]

    failed = peer_review.run_after_trading(completed, PACKET,
                                          runner=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")))
    assert failed["status"] == "failed" and "unavailable" in failed["error"]
    assert all(row["status"] == "completed" for row in completed.values())


def test_shared_output_api_publishes_replies_but_remains_read_only(monkeypatch):
    from ai_portfolios import shared_output_server

    monkeypatch.setattr(shared_output_server, "read_all_teams", lambda: {"teams": [{"team_id": "claude-independent"}]})
    monkeypatch.setattr(shared_output_server, "read_discussions", lambda: [{"parent_post_id": "r:claude"}])
    server = shared_output_server.create_server(0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        response = requests.get(base + "/api/teams", timeout=2)
        assert response.status_code == 200
        assert response.json()["discussions"] == [{"parent_post_id": "r:claude"}]
        assert requests.post(base + "/api/teams", json={}, timeout=2).status_code == 405
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
