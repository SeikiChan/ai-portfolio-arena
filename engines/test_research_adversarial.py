"""Independent offline tests of the research boundary; never calls external services."""
import base64
import io
import json
import socket
from unittest.mock import Mock

import pytest
from pypdf import PdfWriter

from engines import research_tools as rt
from engines import research
from copy import deepcopy


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=AssertionError("unexpected network")))
    monkeypatch.setattr(socket, "getaddrinfo", Mock(side_effect=AssertionError("unexpected DNS")))


def dns(monkeypatch, *addresses):
    lookup = Mock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses])
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    return lookup


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://example.org", "https://user:pass@example.org", "https://example.org:8080", "https://example.org/\r\nHeader:x", "https://example.org\\@localhost", "https://paper-api.alpaca.markets/v2/orders", "https://API.ALPACA.MARKETS./v2/orders"])
def test_unsafe_urls_rejected_before_dns(url):
    with pytest.raises(ValueError):
        rt.safe_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.2", "172.16.0.1", "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "224.0.0.1", "ff02::1"])
def test_nonpublic_dns_answers_rejected(monkeypatch, address):
    dns(monkeypatch, address)
    with pytest.raises(ValueError, match="Non-public"):
        rt.safe_url("https://example.org")


def test_mixed_dns_is_rejected_even_when_first_address_is_public(monkeypatch):
    dns(monkeypatch, "8.8.8.8", "127.0.0.1")
    with pytest.raises(ValueError):
        rt.safe_url("https://example.org")


def test_connection_is_pinned_and_tls_uses_hostname(monkeypatch):
    raw = Mock()
    connect = Mock(return_value=raw)
    monkeypatch.setattr(socket, "create_connection", connect)
    connection = rt._PinnedHTTPS("example.org", "8.8.8.8")
    context = Mock()
    connection._context = context
    connection.connect()
    connect.assert_called_once_with(("8.8.8.8", 443), 12)
    context.wrap_socket.assert_called_once_with(raw, server_hostname="example.org")


def response(status=200, body=b"hello", **headers):
    result = Mock(status=status)
    result.getheader.side_effect = lambda name, default=None: headers.get(name, default)
    result.read.return_value = body
    return result


def fake_connection(monkeypatch, reply):
    conn = Mock()
    conn.getresponse.return_value = reply
    factory = Mock(return_value=conn)
    monkeypatch.setattr(rt, "_PinnedHTTPS", factory)
    return factory, conn


def test_redirect_revalidates_destination_before_connect(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1" if host == "localhost" else "8.8.8.8", 443))])
    factory, conn = fake_connection(monkeypatch, response(302, Location="https://localhost/private"))
    with pytest.raises(ValueError, match="Non-public"):
        rt.fetch_url("https://example.org")
    assert factory.call_count == 1
    conn.close.assert_called_once()


@pytest.mark.parametrize("reply", [response(200, **{"Content-Encoding": "gzip"}), response(200, **{"Content-Length": str(rt.MAX_BYTES + 1)}), response(200, body=b"x" * (rt.MAX_BYTES + 1)), response(403), response(302)])
def test_download_failures_close_connection(monkeypatch, reply):
    dns(monkeypatch, "8.8.8.8")
    _, conn = fake_connection(monkeypatch, reply)
    with pytest.raises(ValueError):
        rt.fetch_url("https://example.org")
    conn.close.assert_called_once()


def test_redirect_loop_is_bounded(monkeypatch):
    dns(monkeypatch, "8.8.8.8")
    factory, conn = fake_connection(monkeypatch, response(302, Location="/again"))
    with pytest.raises(ValueError, match="redirects"):
        rt.fetch_url("https://example.org")
    assert factory.call_count == 6
    assert conn.close.call_count == 6


@pytest.mark.parametrize("expression", ["__import__('os').getcwd()", "(1).__class__", "[1][0]", "True", "2**100000", "1/0", "1e101", "1e309", "1+" * 160 + "1", "'hello'"])
def test_calculator_rejects_code_and_unbounded_arithmetic(expression):
    with pytest.raises((ValueError, SyntaxError, ZeroDivisionError)):
        rt.calculate(expression)


def test_calculator_usable_arithmetic():
    assert rt.calculate("(120 - 100) / 100 * 100") == 20


def test_html_removes_scripts_and_keeps_relative_source_links():
    result = rt.document({"url": "https://example.org/research/", "content_type": "text/html; charset=utf-8", "body": b'<style>secret-css</style><script>secret-js</script><p>Revenue rose &amp; profits fell.</p><a href="report.pdf">Report</a>'})
    assert "secret" not in result["text"]
    assert "Revenue rose & profits fell." in result["text"]
    assert result["links"] == ["https://example.org/research/report.pdf"]


def test_failed_decode_is_explicit():
    with pytest.raises(UnicodeDecodeError):
        rt.document({"url": "https://example.org", "content_type": "text/plain", "body": b"\xff"})


def test_blank_pdf_is_unknown_not_invented_text():
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    content = io.BytesIO()
    writer.write(content)
    with pytest.raises(ValueError, match="no extractable text"):
        rt.document({"url": "https://example.org/doc.pdf", "content_type": "application/pdf", "body": content.getvalue()})


def test_xml_entity_declaration_rejected():
    with pytest.raises(ValueError, match="declarations"):
        rt._feed(b'<!DOCTYPE rss [<!ENTITY x "bad">]><rss><channel/></rss>')


def test_source_bytes_preserved_for_audit(monkeypatch):
    source = {"url": "https://example.org", "content_type": "text/plain", "body": b"Observed statement"}
    monkeypatch.setattr(rt, "fetch_url", lambda url: source)
    result = rt.execute_tool({"tool": "read_url", "url": source["url"]})
    assert result["classification"] == "observed"
    assert base64.b64decode(result["raw_base64"]) == source["body"]


@pytest.mark.parametrize("action", [{"tool": "shell", "expression": "pwd"}, {"tool": "calculate", "expression": "1/0"}, {"tool": "read_url", "url": "file:///secret"}])
def test_worker_reports_unknown_instead_of_success(monkeypatch, capsys, action):
    monkeypatch.setattr(rt.sys, "stdin", io.StringIO(json.dumps(action)))
    rt.main()
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unknown"
    assert result["classification"] == "unknown"
    assert "text" not in result


def action(**overrides):
    return {"tool": "search_web", "query": "MSFT earnings", "url": "", "symbol": "MSFT", "start_page": 1, "expression": "", **overrides}


def packet():
    return {"snapshot_id": "frozen-test", "frozen_at": "2026-10-01T00:00:00Z", "features": [], "evidence": [], "errors": [], "private_account": "DO_NOT_SEND_TO_RESEARCH"}


CONFIG = {"universe": ["MSFT"], "model_timeout_seconds": 30}


def test_loop_bounds_calls_and_preserves_original_packet(tmp_path):
    base = packet()
    before = deepcopy(base)
    prompts = []
    calls = []
    def planner(provider, prompt, *args):
        assert provider == "claude"
        prompts.append(prompt)
        return {"done": False, "actions": [action(), action(), action()]}
    def executor(request, timeout):
        calls.append(request)
        assert timeout <= research.TOOL_TIMEOUT
        return {"status": "unknown", "classification": "unknown", "error": "Source unavailable"}
    result = research.run("claude", base, CONFIG, tmp_path, planner=planner, executor=executor)
    assert base == before
    assert len(calls) == research.MAX_CALLS
    assert len(prompts) <= research.MAX_STEPS
    assert all("DO_NOT_SEND_TO_RESEARCH" not in p for p in prompts)
    assert result["evidence"] == []
    assert len(result["errors"]) == len(calls)
    assert len(list(tmp_path.glob("research_*_request.json"))) == len(calls)
    assert len(list(tmp_path.glob("research_*_result.json"))) == len(calls)


def test_research_source_integrity_mismatch_fails_round(tmp_path):
    planner = lambda *args: {"done": False, "actions": [action()]}
    executor = lambda *args: {"status": "ok", "classification": "observed", "raw_base64": base64.b64encode(b"actual").decode(), "source_sha256": "wrong", "text": "invented"}
    with pytest.raises(ValueError, match="digest mismatch"):
        research.run("gpt", packet(), CONFIG, tmp_path, planner=planner, executor=executor)
    assert not (tmp_path / "decision_packet.json").exists()
    assert json.loads((tmp_path / "research_manifest.json").read_text())["status"] == "failed"


def test_invalid_planner_response_is_archived_as_failure(tmp_path):
    planner = lambda *args: {"done": False, "actions": [action(tool="shell")]}
    executor = Mock(side_effect=AssertionError("invalid tool executed"))
    with pytest.raises(ValueError):
        research.run("gpt", packet(), CONFIG, tmp_path, planner=planner, executor=executor)
    executor.assert_not_called()
    manifest = json.loads((tmp_path / "research_manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["tool_calls"] == 0


@pytest.mark.parametrize("overrides", [{"symbol": "SPY"}, {"start_page": True}, {"start_page": 0}, {"start_page": 10001}, {"tool": "execute_order"}, {"extra": "x"}, {"query": None}])
def test_invalid_actions_rejected(overrides):
    with pytest.raises(ValueError):
        research.validate_actions({"done": False, "actions": [action(**overrides)]}, ["MSFT"])


def test_worker_environment_does_not_inherit_secrets(monkeypatch):
    for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "HTTP_PROXY", "HTTPS_PROXY", "PYTHONPATH"]:
        monkeypatch.setenv(key, "SECRET_VALUE")
    worker = research._environment()
    assert "SECRET_VALUE" not in worker.values()
    assert "OPENAI_API_KEY" not in research._environment("claude")
    assert "ANTHROPIC_API_KEY" not in research._environment("gpt")


def test_existing_decision_packet_is_never_overwritten(tmp_path):
    target = tmp_path / "decision_packet.json"
    target.write_text("old artifact", encoding="utf-8")
    with pytest.raises(FileExistsError):
        research.run("gpt", packet(), CONFIG, tmp_path, planner=lambda *args: {"done": True, "actions": []})
    assert target.read_text(encoding="utf-8") == "old artifact"


def test_experiment_identity_changes_with_provider_config_and_mandate():
    engine = Mock(engine_id="claude_engine", version="test-v1")
    engine.instruction.return_value = "fixed instruction"
    baseline = research.identity(engine, CONFIG, "original mandate")
    assert research.identity(engine, deepcopy(CONFIG), "original mandate") == baseline
    assert research.identity(engine, CONFIG, "changed mandate") != baseline
    assert research.identity(engine, {**CONFIG, "universe": ["MSFT", "AAPL"]}, "original mandate") != baseline
    engine.engine_id = "codex_engine"
    assert research.identity(engine, CONFIG, "original mandate") != baseline


def test_unsupported_provider_cannot_invoke_planner(tmp_path):
    planner = Mock(side_effect=AssertionError("planner invoked"))
    with pytest.raises(ValueError, match="provider"):
        research.run("other", packet(), CONFIG, tmp_path, planner=planner)
    planner.assert_not_called()


def test_source_archive_matches_manifest_and_evidence(tmp_path):
    import hashlib
    body = b"Source evidence"
    digest = hashlib.sha256(body).hexdigest()
    outputs = iter([{"done": False, "actions": [action()]}, {"done": True, "actions": []}])
    planner = lambda *args: next(outputs)
    executor = lambda *args: {"status": "ok", "classification": "observed", "text": body.decode(), "source_sha256": digest, "raw_base64": base64.b64encode(body).decode()}
    result = research.run("gpt", packet(), CONFIG, tmp_path, planner=planner, executor=executor)
    assert (tmp_path / "research_01_source.bin").read_bytes() == body
    manifest = json.loads((tmp_path / "research_manifest.json").read_text())
    assert manifest["files"]["research_01_source.bin"] == digest
    assert result["evidence"][0]["data"]["source_sha256"] == digest
    assert "raw_base64" not in result["evidence"][0]["data"]
    assert result["base_frozen_at"] == packet()["frozen_at"]
    assert result["research"]["provider"] == "gpt"
