import json
import subprocess
from unittest.mock import Mock

import pytest

from engines import research, research_tools


def test_scholarly_truncation_is_explicit(monkeypatch):
    rows = [{"title": ["A long paper title"], "abstract": "x" * 4000, "URL": "https://example.org/paper"} for _ in range(6)]
    source = {"url": "https://api.crossref.org/works", "content_type": "application/json", "body": json.dumps({"message": {"items": rows}}).encode()}
    monkeypatch.setattr(research_tools, "fetch_url", lambda url: source)
    result = research_tools.execute_tool({"tool": "search_scholarly", "query": "portfolio"})
    assert len(result["text"]) == research_tools.MAX_TEXT
    assert result.get("truncated") is True


def test_planner_timeout_preserves_partial_output(monkeypatch, tmp_path):
    monkeypatch.setattr(research, "_cli", lambda provider: "fake-cli")
    monkeypatch.setattr(research.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired("fake-cli", 1, output=b"partial model result", stderr=b"diagnostic")))
    with pytest.raises(research.ResearchFailure) as raised:
        research.invoke("claude", "prompt", {"claude_model": "fake"}, tmp_path, 0, 1)
    assert raised.value.failure_kind == "timeout"
    path = tmp_path / "research_00_claude_process.json"
    assert path.exists()
    result = json.loads(path.read_text(encoding="utf-8"))
    assert "partial model result" in result["stdout"]
    assert "diagnostic" in result["stderr"]
