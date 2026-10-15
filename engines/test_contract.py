from copy import deepcopy

import pytest

from ai_portfolios.common import digest, write
from engines import contract, publish, registry

PACKET = {"snapshot_id": "s1", "frozen_at": "2026-10-09T15:00:00+00:00", "features": [], "evidence": [], "errors": []}


def round_dir(tmp_path, prompt_body="prompt", **overrides):
    directory = tmp_path / "20261010T000000000000Z"
    directory.mkdir()
    write(directory / "packet.json", PACKET)
    job = {"id": directory.name, "engine_id": "claude_engine", "engine_version": "2026-10-10.v3-paper-execution",
           "engine_author": "Claude", "providers": ["claude"], "environment": "alpaca_paper",
           "packet_id": "packet-1", "packet_frozen_at": "2026-10-09T15:00:00+00:00",
           "packet_sha256": digest(directory / "packet.json"), "instruction_sha256": "a" * 64,
           "mandate_sha256": {"claude": "b" * 64}, "strict_comparison": False,
           "account_snapshot_id": "20261010T000000Z",
           "account_snapshot": {"provider": "claude", "account_id_sha256": "c" * 64, "files": {}},
           "reasoning_effort": "medium", "orders_submitted": False, "status": "completed",
           "started_at": "2026-10-10T00:00:00+00:00", "completed_at": "2026-10-10T00:05:00+00:00"}
    job.update(overrides)
    account_dir = directory / "account_snapshot"
    account_dir.mkdir()
    write(account_dir / "snapshot.json", {"provider": job["account_snapshot"]["provider"], "status": "connected"})
    job["account_snapshot"]["files"] = {"snapshot.json": digest(account_dir / "snapshot.json")}
    for provider in job["providers"] if isinstance(job["providers"], list) else []:
        (directory / f"{provider}_prompt.txt").write_text(prompt_body, encoding="utf-8")
        write(directory / f"{provider}_status.json", {"provider": provider, "status": "validated", "orders_submitted": False,
              "model_requested": "m", "decision": {"overview": "o", "cash_reason": "c", "evidence_gaps": [],
              "macro_claims": [], "memory_claims": [], "injection_flags": [], "positions": [
                  {"symbol": "MSFT", "action": "new", "target_weight": 0.1, "horizon_days": 60,
                   "confidence": 0.6, "thesis": "t", "changes_vs_last": "c", "evidence_ids": [],
                   "fact_refs": [], "evidence_quotes": [], "invalidation": {}}]}})
    write(directory / "round.json", job)
    write(directory / "receipt.json", {"round_id": directory.name, "engine_id": job["engine_id"],
          "packet_id": job["packet_id"], "packet_sha256": job["packet_sha256"],
          "files": {path.name: digest(path) for path in directory.iterdir() if path.is_file()}})
    return directory


def test_valid_round_passes_the_contract(tmp_path):
    summary = contract.validate_round(round_dir(tmp_path))
    assert summary["providers"] == ["claude"] and summary["strict_comparison"] is False


def test_claude_engine_conforms_to_the_interface():
    details = contract.check_engine(registry.get("claude_engine"))
    assert details["author"] == "Claude" and details["instruction_chars"] > 1000


def test_both_independent_engine_slots_are_implemented():
    engines, problems = registry.loaded()
    assert "codex_engine" in registry.declared()
    assert "claude_engine" in engines and "codex_engine" in engines
    assert not problems


@pytest.mark.parametrize("value", [True, None, 0, "false"])
def test_orders_submitted_must_be_exactly_false(tmp_path, value):
    with pytest.raises(ValueError, match="orders_submitted"):
        contract.validate_round(round_dir(tmp_path, orders_submitted=value))


def test_packet_hash_mismatch_rejected(tmp_path):
    with pytest.raises(ValueError, match="packet_sha256"):
        contract.validate_round(round_dir(tmp_path, packet_sha256="c" * 64))


def test_strict_round_cannot_have_differing_mandates(tmp_path):
    with pytest.raises(ValueError, match="byte-identical"):
        contract.validate_round(round_dir(tmp_path, engine_id="test_engine", providers=["claude", "gpt"],
            mandate_sha256={"claude": "b" * 64, "gpt": "d" * 64}, strict_comparison=True))


def test_strict_round_cannot_be_a_single_provider(tmp_path):
    with pytest.raises(ValueError, match="byte-identical"):
        contract.validate_round(round_dir(tmp_path, engine_id="test_engine", providers=["claude"],
            mandate_sha256={"claude": "b" * 64}, strict_comparison=True))


def test_single_provider_round_is_allowed_when_not_called_strict(tmp_path):
    summary = contract.validate_round(round_dir(tmp_path, providers=["claude"], strict_comparison=False,
                                                mandate_sha256={"claude": "b" * 64}))
    assert summary["providers"] == ["claude"] and summary["strict_comparison"] is False


@pytest.mark.parametrize("field", sorted(contract.ROUND_REQUIRED))
def test_every_required_field_is_actually_required(tmp_path, field):
    directory = round_dir(tmp_path)
    job = {k: v for k, v in __import__("json").loads((directory / "round.json").read_text(encoding="utf-8")).items() if k != field}
    write(directory / "round.json", job)
    with pytest.raises(ValueError):
        contract.validate_round(directory)


@pytest.mark.parametrize("missing", ["packet.json", "receipt.json", "claude_prompt.txt", "claude_status.json"])
def test_missing_required_file_rejected(tmp_path, missing):
    directory = round_dir(tmp_path)
    (directory / missing).unlink()
    with pytest.raises(ValueError):
        contract.validate_round(directory)


@pytest.mark.parametrize("providers", [[], ["claude", "claude"], ["claude", "grok"], "claude"])
def test_bad_provider_list_rejected(tmp_path, providers):
    mandates = {p: "b" * 64 for p in providers} if isinstance(providers, list) else {"claude": "b" * 64}
    with pytest.raises(ValueError):
        contract.validate_round(round_dir(tmp_path, providers=providers, mandate_sha256=mandates,
                                          strict_comparison=False))


def test_diagonal_only_design_is_flagged_as_inseparable():
    diagonal = [{"engine_id": "claude_engine", "providers": ["claude"]},
                {"engine_id": "codex_engine", "providers": ["gpt"]}]
    report = contract.cell_coverage(diagonal)
    assert report["separable"] is False
    assert sorted(report["empty_cells"]) == ["claude_engine/gpt", "codex_engine/claude"]


def test_full_design_is_separable():
    full = [{"engine_id": "claude_engine", "providers": ["claude", "gpt"]},
            {"engine_id": "codex_engine", "providers": ["claude", "gpt"]}]
    assert contract.cell_coverage(full)["separable"] is True


def test_single_engine_is_never_separable():
    assert contract.cell_coverage([{"engine_id": "claude_engine", "providers": ["claude", "gpt"]}])["separable"] is False


@pytest.mark.parametrize("engine_id", ["", "Claude", "claude-engine", "ab", "../etc", "a" * 40])
def test_bad_engine_id_cannot_address_a_result_root(engine_id):
    with pytest.raises(ValueError):
        contract.result_root(engine_id)


def test_export_publishes_one_entry_per_model(tmp_path, monkeypatch):
    directory = round_dir(tmp_path)
    monkeypatch.setattr(contract, "result_root", lambda engine_id: directory.parent)
    export = publish.build()
    assert export["schema_version"] == 1 and export["team_id"] == "claude-independent"
    assert export["status"] == "available" and export["execution_mode"] == "alpaca_paper_auto"
    assert [row["provider"] for row in export["rounds"]] == ["claude"]
    assert [row["id"] for row in export["rounds"]] == [f"{directory.name}:claude"]
    for row in export["rounds"]:
        for field in ("id", "completed_at", "status", "user_prompt", "full_prompt"):
            assert field in row


def test_published_equity_marks_are_observed_and_versioned(tmp_path, monkeypatch):
    directory = round_dir(tmp_path, experiment_id="paper-v2")
    account_file = directory / "account_snapshot" / "claude_account.json"
    write(account_file, {"equity": "10042.75", "account_number": "SECRET"})
    job = __import__("json").loads((directory / "round.json").read_text(encoding="utf-8"))
    job["account_snapshot"]["files"][account_file.name] = digest(account_file)
    write(directory / "round.json", job)
    receipt = __import__("json").loads((directory / "receipt.json").read_text(encoding="utf-8"))
    receipt["files"]["round.json"] = digest(directory / "round.json")
    write(directory / "receipt.json", receipt)
    monkeypatch.setattr(contract, "result_root", lambda engine_id: directory.parent)

    export = publish.build()
    mark = export["equity_history"][0]
    assert mark["equity"] == 10042.75 and mark["experiment_id"] == "paper-v2"
    assert mark["at"] == "20261010T000000Z"
    assert "SECRET" not in __import__("json").dumps(export)


def test_portfolio_history_is_read_only_sanitized_and_split_by_known_experiment(monkeypatch):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    first, second = now - timedelta(days=3), now - timedelta(days=1)
    marks = [{"at": first.isoformat(), "experiment_id": "exp-a"},
             {"at": second.isoformat(), "experiment_id": "exp-b"}]
    requested = {}

    class Response:
        status_code = 200
        def json(self):
            return {"timestamp": [int((second + timedelta(hours=2)).timestamp()),
                                  int((first + timedelta(hours=1)).timestamp()),
                                  int((second + timedelta(hours=1)).timestamp())],
                    "equity": ["10012.50", "10000.00", "bad-value"]}

    def getter(url, **kwargs):
        requested["url"] = url
        requested.update(kwargs)
        return Response()

    monkeypatch.setattr(publish, "paper_credentials", lambda *, only: {only: ("PAPER_KEY", "PAPER_SECRET")})
    history = publish.portfolio_history("codex_engine", marks, getter=getter)

    assert requested["url"] == "https://paper-api.alpaca.markets/v2/account/portfolio/history"
    assert requested["headers"] == {"APCA-API-KEY-ID": "PAPER_KEY", "APCA-API-SECRET-KEY": "PAPER_SECRET"}
    assert requested["params"]["timeframe"] == "15Min"
    assert history["status"] == "available" and history["invalid_rows"] == 1
    assert [point["experiment_id"] for point in history["points"]] == ["exp-a", "exp-b"]
    assert [point["equity"] for point in history["points"]] == [10000.0, 10012.5]
    serialized = __import__("json").dumps(history)
    assert "PAPER_KEY" not in serialized and "PAPER_SECRET" not in serialized


def test_compact_snapshot_timestamp_parser_keeps_fractional_seconds():
    from datetime import datetime, timezone
    parsed = publish._utc_datetime("20261015T133518291686Z")
    assert parsed == datetime(2026, 10, 15, 13, 35, 18, 291686, tzinfo=timezone.utc)


def test_unavailable_broker_history_does_not_fabricate_a_series(monkeypatch):
    monkeypatch.setattr(publish, "paper_credentials", lambda *, only: {only: ("PAPER_KEY", "PAPER_SECRET")})
    def timeout(*args, **kwargs):
        raise publish.requests.Timeout()
    result = publish.portfolio_history("claude_engine", [{"at": "20261010T000000Z", "experiment_id": "exp"}], getter=timeout)
    assert result["status"] == "unavailable" and result["reason"] == "Timeout"
    assert result["points"] == []


def test_research_trace_excludes_model_input_and_bounds_tool_excerpt(tmp_path):
    directory = tmp_path / "round"
    directory.mkdir()
    write(directory / "research_00_gpt_prompt.json", {"prompt": "private account input"})
    write(directory / "research_00_gpt_response.json", {"done": False, "actions": [{"tool": "search_web", "query": "earnings"}]})
    write(directory / "research_01_request.json", {"action": {"tool": "search_web", "query": "earnings"}})
    write(directory / "research_01_result.json", {"status": "ok", "classification": "observed", "text": "x" * 5000})
    write(directory / "research_01_gpt_response.json", {"done": True, "actions": []})

    trace = publish.research_trace(directory, "gpt")
    assert [row["kind"] for row in trace] == ["model", "tool", "model"]
    assert len(trace[1]["text"]) == 4000 and trace[1]["excerpt_truncated"] is True
    assert "private account input" not in __import__("json").dumps(trace)


def test_export_never_carries_secrets_or_full_account_identity(tmp_path, monkeypatch):
    # The prompt body has to be in place before round_dir seals the receipt: rewriting a file
    # afterwards is exactly the tampering validate_round now rejects.
    directory = round_dir(tmp_path, prompt_body="RULES stay." + chr(10) + "INPUT:" + chr(10)
                          + '{"alpaca_paper_portfolio": {"cash": 10000.0}}')
    monkeypatch.setattr(contract, "result_root", lambda engine_id: directory.parent)
    export = publish.build()
    entry = export["rounds"][0]
    assert "RULES stay." in entry["full_prompt"] and "10000" not in entry["full_prompt"]
    assert entry["full_prompt_sha256"] and entry["full_prompt_chars"] > 0
    assert export["orders_submitted"] is False
    body = __import__("json").dumps(export, ensure_ascii=False)
    for forbidden in ('"account_id": "', "APCA_API_SECRET_KEY", "GptAPCA_API_KEY_ID", "ClaudeAPCA_API_KEY_ID"):
        assert forbidden not in body
    position = export["rounds"][0]["positions"][0]
    assert set(position) == set(publish.POSITION_FIELDS)


def test_invalid_round_is_reported_not_dropped(tmp_path, monkeypatch):
    directory = round_dir(tmp_path, orders_submitted=True)
    monkeypatch.setattr(contract, "result_root", lambda engine_id: directory.parent)
    export = publish.build()
    assert export["rounds"] == [] and export["status"] == "unavailable"
    assert export["branch"] and export["workspace"]
    assert export["rejected_rounds"][0]["round_id"] == directory.name
    assert "orders_submitted" in export["rejected_rounds"][0]["reason"]


def test_engine_rejects_an_unknown_provider():
    with pytest.raises(ValueError, match="only the Claude"):
        registry.get("claude_engine").decide("grok", deepcopy(PACKET), {}, "", None, None)
