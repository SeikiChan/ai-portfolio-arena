import json

import pytest
import requests

from .team_exports import TEAMS, read_all_teams, read_team_export
from .test_console import web


def test_every_registered_team_is_reported_even_when_it_has_published_nothing():
    teams = {team["team_id"] for team in read_all_teams()["teams"]}
    assert teams == set(TEAMS)
    assert "claude-independent" in teams and "gpt-independent" in teams


def test_missing_publication_is_unavailable_never_an_empty_success(tmp_path):
    report = read_team_export("claude-independent", tmp_path / "absent.json")
    assert report["status"] == "unavailable" and report["rounds"] == []


def test_a_team_cannot_publish_under_another_team_id(tmp_path):
    path = tmp_path / "dashboard.json"
    path.write_text(json.dumps({"schema_version": 1, "team_id": "gpt-independent", "rounds": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_team_export("claude-independent", path)


@pytest.mark.parametrize("payload", [{"schema_version": 2, "team_id": "claude-independent", "rounds": []},
                                     {"schema_version": 1, "team_id": "claude-independent", "rounds": {}},
                                     {"team_id": "claude-independent", "rounds": []}])
def test_malformed_publication_rejected(tmp_path, payload):
    path = tmp_path / "dashboard.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        read_team_export("claude-independent", path)


def test_teams_endpoint_is_read_only(web):
    base, _ = web
    listing = requests.get(base + "/api/teams").json()
    assert {team["team_id"] for team in listing["teams"]} == set(TEAMS)
    token = requests.get(base + "/api/state").json()["csrf_token"]
    assert requests.post(base + "/api/teams", json={}, headers={"X-Console-Token": token}).status_code == 404
