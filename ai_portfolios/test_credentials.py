import json

import pytest

from ai_portfolios import credentials


@pytest.fixture
def shared(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "SECRETS", tmp_path / "secrets")
    monkeypatch.setattr(credentials, "ROOT", tmp_path / "workspace")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "workspace").mkdir()
    for name in list(credentials.MARKET_KEYS) + ["ClaudeAPCA_API_KEY_ID", "GptAPCA_API_KEY_ID"]:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def write_market(shared, body):
    (shared / "secrets" / credentials.MARKET_DATA_FILE).write_text(body, encoding="utf-8")


def write_accounts(shared, body):
    (shared / "secrets" / credentials.PAPER_ACCOUNTS_FILE).write_text(body, encoding="utf-8")


ACCOUNTS = ("ClaudeAPCA_API_KEY_ID=PKclaude\nClaudeAPCA_API_SECRET_KEY=cs\n"
            "GptAPCA_API_KEY_ID=PKgpt\nGptAPCA_API_SECRET_KEY=gs\n")


def test_shared_file_is_used_before_a_workspace_local_one(shared):
    write_market(shared, "APCA_API_KEY_ID=PKshared\nAPCA_API_SECRET_KEY=s\nFMP_API_KEY=f\n")
    (shared / "workspace" / ".env").write_text("APCA_API_KEY_ID=PKlocal\n", encoding="utf-8")
    assert credentials.market_data_keys()["APCA_API_KEY_ID"] == "PKshared"


def test_workspace_local_file_still_works_when_nothing_is_shared(shared):
    (shared / "workspace" / ".env").write_text("APCA_API_KEY_ID=PKlocal\nAPCA_API_SECRET_KEY=s\n", encoding="utf-8")
    assert credentials.market_data_keys()["APCA_API_KEY_ID"] == "PKlocal"


def test_environment_wins_over_every_file(shared, monkeypatch):
    write_market(shared, "APCA_API_KEY_ID=PKshared\n")
    monkeypatch.setenv("APCA_API_KEY_ID", "PKenv")
    assert credentials.market_data_keys()["APCA_API_KEY_ID"] == "PKenv"


def test_missing_value_is_none_never_a_guess(shared):
    assert credentials.market_data_keys() == {name: None for name in credentials.MARKET_KEYS}


def test_both_teams_resolve_the_same_market_data_key(shared):
    write_market(shared, "APCA_API_KEY_ID=PKshared\nAPCA_API_SECRET_KEY=s\nFMP_API_KEY=f\n")
    first = credentials.market_data_keys()
    second = credentials.market_data_keys()
    assert first == second
    assert credentials.fingerprint(first["APCA_API_KEY_ID"]) == credentials.fingerprint(second["APCA_API_KEY_ID"])


def test_one_team_can_load_only_its_own_account(shared):
    write_accounts(shared, ACCOUNTS)
    assert credentials.paper_credentials(only="gpt") == {"gpt": ("PKgpt", "gs")}
    assert "claude" not in credentials.paper_credentials(only="gpt")


def test_unknown_account_name_rejected(shared):
    write_accounts(shared, ACCOUNTS)
    with pytest.raises(ValueError, match="未知的账户名"):
        credentials.paper_credentials(only="grok")


def test_two_accounts_sharing_one_key_rejected(shared):
    write_accounts(shared, ACCOUNTS.replace("PKgpt", "PKclaude"))
    with pytest.raises(ValueError, match="相同"):
        credentials.paper_credentials()


def test_incomplete_account_pair_rejected(shared):
    write_accounts(shared, "ClaudeAPCA_API_KEY_ID=PKclaude\n")
    with pytest.raises(ValueError, match="尚未完整配置"):
        credentials.paper_credentials(only="claude")


def test_duplicate_variable_in_a_secrets_file_rejected(shared):
    write_market(shared, "APCA_API_KEY_ID=a\nAPCA_API_KEY_ID=b\n")
    with pytest.raises(ValueError, match="重复变量名"):
        credentials.market_data_keys()


def test_audit_reports_provenance_and_never_a_secret(shared):
    write_market(shared, "APCA_API_KEY_ID=PKdata\nAPCA_API_SECRET_KEY=supersecret\nFMP_API_KEY=fmpsecret\n")
    write_accounts(shared, ACCOUNTS)
    report = credentials.audit()
    body = json.dumps(report, ensure_ascii=False)
    for secret in ("PKdata", "supersecret", "fmpsecret", "PKclaude", "PKgpt", "cs", "gs"):
        assert secret not in body
    assert report["credentials"]["APCA_API_KEY_ID"]["source"].endswith(credentials.MARKET_DATA_FILE)
    assert report["credentials"]["APCA_API_KEY_ID"]["fingerprint"]
    # A secret half must never carry a fingerprint either: only presence and provenance.
    assert set(report["credentials"]["APCA_API_SECRET_KEY"]) == {"present", "source"}
    assert report["credentials"]["APCA_API_SECRET_KEY"]["present"] is True


def test_audit_flags_a_data_key_that_is_really_a_team_account(shared):
    write_accounts(shared, ACCOUNTS)
    write_market(shared, "APCA_API_KEY_ID=PKgpt\nAPCA_API_SECRET_KEY=gs\n")
    assert credentials.audit()["market_data_key_is_neutral"] is False
    write_market(shared, "APCA_API_KEY_ID=PKdata\nAPCA_API_SECRET_KEY=s\n")
    assert credentials.audit()["market_data_key_is_neutral"] is True


def test_missing_data_key_is_not_reported_as_neutral(shared):
    write_accounts(shared, ACCOUNTS)
    assert credentials.audit()["market_data_key_is_neutral"] is False


def test_secrets_directory_is_overridable_by_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ARENA_SECRETS", str(tmp_path / "elsewhere"))
    import importlib

    reloaded = importlib.reload(credentials)
    try:
        assert reloaded.SECRETS == tmp_path / "elsewhere"
    finally:
        monkeypatch.delenv("AI_ARENA_SECRETS")
        importlib.reload(credentials)
