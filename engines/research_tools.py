"""Read-only public research tools. No credentials, local file tools, or arbitrary code execution."""
from __future__ import annotations

import ast
import base64
import hashlib
import http.client
import io
import ipaddress
import json
import math
import operator
import socket
import ssl
import sys
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit
from xml.etree import ElementTree

MAX_BYTES = 4 * 1024 * 1024
MAX_TEXT = 24000
TOOLS = {"search_web", "search_news", "search_papers", "search_scholarly", "read_url", "calculate"}


def safe_url(url):
    """Resolve once, reject mixed/private DNS, and return the IP to pin the connection to."""
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 33 for c in url) or "\\" in url:
        raise ValueError("Invalid public HTTPS URL")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
        raise ValueError("Only public HTTPS on port 443 is allowed")
    host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    if host in {"api.alpaca.markets", "paper-api.alpaca.markets"}:
        raise ValueError("Broker endpoints are not research tools")
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(address).is_global or
                            ipaddress.ip_address(address).is_multicast for address in addresses):
        raise ValueError("Non-public address is forbidden")
    return host, addresses[0], (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=12, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # DNS is not consulted again; TLS still authenticates the original hostname.
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def fetch_url(url):
    """No cookies, authorization, proxies, automatic redirects, or compressed responses."""
    for _ in range(6):
        host, address, path = safe_url(url)
        connection = _PinnedHTTPS(host, address)
        try:
            connection.request("GET", path, headers={"User-Agent": "AI-Portfolio-Arena-Research/1.0",
                                                     "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Redirect has no location")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"Public source returned HTTP {response.status}")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("Compressed HTTP response is not supported")
            size = response.getheader("Content-Length")
            if size and int(size) > MAX_BYTES:
                raise ValueError("Source exceeds download limit")
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("Source exceeds download limit")
            return {"url": url, "content_type": response.getheader("Content-Type", ""), "body": body}
        finally:
            connection.close()
    raise ValueError("Too many redirects")


class _HTMLText(HTMLParser):
    def __init__(self, url=""):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []
        self.links = []
        self.url = url

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if tag == "a" and not self.hidden:
            href = dict(attrs).get("href", "")
            target = urljoin(self.url, href)
            if target.startswith("https://") and target not in self.links and len(self.links) < 60:
                self.links.append(target)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _html(text, url=""):
    parser = _HTMLText(url)
    parser.feed(text)
    return " ".join("".join(parser.parts).split()), parser.links


def document(source, start_page=1):
    body = source["body"]
    mime = source["content_type"].split(";", 1)[0].lower()
    if body.startswith(b"%PDF-") or mime == "application/pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF is not readable")
        if not 1 <= start_page <= len(reader.pages):
            raise ValueError("PDF page is outside the document")
        end = min(start_page + 4, len(reader.pages))
        text = "\n".join(f"[Page {i + 1}]\n{reader.pages[i].extract_text() or ''}"
                         for i in range(start_page - 1, end))
        if not any(c.isalnum() for c in text.replace("Page", "") if not c.isdigit()):
            raise ValueError("PDF has no extractable text; OCR is not available")
        return {"text": text[:MAX_TEXT], "pages": len(reader.pages), "start_page": start_page,
                "end_page": end, "truncated": len(text) > MAX_TEXT or end < len(reader.pages)}
    if mime not in {"text/html", "application/xhtml+xml", "text/plain", "application/json", "application/xml", "text/xml"}:
        raise ValueError("Unsupported document type")
    # Failed decoding is explicit; do not silently invent characters in quotations.
    charset = "utf-8"
    import re
    match = re.search(r"charset=[\"']?([\w-]+)", source["content_type"], re.I)
    if match:
        charset = match.group(1)
    text = body.decode(charset)
    links = []
    if "html" in mime:
        text, links = _html(text, source["url"])
    if not text.strip():
        raise ValueError("Source has no readable text")
    return {"text": text[:MAX_TEXT], "links": links, "truncated": len(text) > MAX_TEXT}


def calculate(expression):
    """Finite arithmetic only: no names, calls, attributes, indexing, powers, or evaluation."""
    if not isinstance(expression, str) or len(expression) > 300:
        raise ValueError("Arithmetic expression is too long")
    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 80:
        raise ValueError("Arithmetic expression is too complex")
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = float(node.value)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        elif isinstance(node, ast.BinOp) and type(node.op) in operations:
            value = operations[type(node.op)](visit(node.left), visit(node.right))
        else:
            raise ValueError("Only numbers and + - * / parentheses are allowed")
        if not math.isfinite(value) or abs(value) > 1e100:
            raise ValueError("Arithmetic result is out of bounds")
        return value

    return visit(tree.body)


def _feed(body, papers=False):
    # Do not accept entity declarations from external XML sources.
    if b"\x00" in body or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("XML declarations are forbidden")
    root = ElementTree.fromstring(body)
    if papers:
        if root.tag != "{http://www.w3.org/2005/Atom}feed":
            raise ValueError("Paper endpoint did not return an Atom feed")
        ns = {"a": "http://www.w3.org/2005/Atom"}
        return [{"title": row.findtext("a:title", default="", namespaces=ns),
                 "url": row.findtext("a:id", default="", namespaces=ns).replace("http://", "https://", 1),
                 "published": row.findtext("a:published", default="", namespaces=ns),
                 "summary": row.findtext("a:summary", default="", namespaces=ns),
                 "pdf": next((x.get("href", "").replace("http://", "https://", 1)
                              for x in row.findall("a:link", ns) if x.get("title") == "pdf"), "")}
                for row in root.findall("a:entry", ns)[:6]]
    if root.tag != "rss":
        raise ValueError("Search endpoint did not return RSS")
    return [{"title": row.findtext("title", ""), "url": row.findtext("link", ""),
             "published": row.findtext("pubDate", ""),
             "summary": _html(row.findtext("description", ""))[0][:2000]}
            for row in root.findall("./channel/item")[:6]]


def execute_tool(action):
    """Return auditable source bytes separately from text shown to the model."""
    tool = action["tool"]
    if tool not in TOOLS:
        raise ValueError("Unknown research tool")
    if tool == "calculate":
        return {"status": "ok", "classification": "derived", "text": str(calculate(action["expression"])),
                "expression": action["expression"], "note": "Arithmetic only; operands are model supplied assumptions unless sourced."}
    if tool == "read_url":
        url = action["url"]
    else:
        query = action["query"]
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            raise ValueError("Search query must contain 1–500 characters")
        if tool == "search_papers":
            url = "https://export.arxiv.org/api/query?" + urlencode({"search_query": query, "max_results": 6})
        elif tool == "search_scholarly":
            url = "https://api.crossref.org/works?" + urlencode({"query.bibliographic": query, "rows": 6})
        elif tool == "search_news":
            url = "https://news.google.com/rss/search?" + urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
        else:
            url = "https://www.bing.com/search?" + urlencode({"q": query, "format": "rss"})
    source = fetch_url(url)
    metadata = {"url": source["url"], "content_type": source["content_type"],
                "source_sha256": hashlib.sha256(source["body"]).hexdigest(),
                "raw_base64": base64.b64encode(source["body"]).decode("ascii")}
    try:
        if tool == "read_url":
            result = document(source, action.get("start_page", 1))
        elif tool == "search_scholarly":
            payload = json.loads(source["body"])
            rows = payload["message"]["items"]
            items = [{"title": row.get("title", []), "url": row.get("URL"), "doi": row.get("DOI"),
                      "published": row.get("published"), "abstract": _html(row.get("abstract", ""))[0][:4000],
                      "links": row.get("link", []), "note": "Bibliographic metadata; not full text or proof of peer review."}
                     for row in rows[:6]]
        else:
            items = _feed(source["body"], tool == "search_papers")
        if tool != "read_url":
            text = json.dumps(items, ensure_ascii=False)
            result = {"text": text[:MAX_TEXT], "result_count": len(items), "truncated": len(text) > MAX_TEXT}
    except Exception as exc:
        return {**metadata, "status": "unknown", "classification": "unknown",
                "error": f"{type(exc).__name__}: {str(exc)[:240]}", "note": "Source bytes archived; text extraction failed."}
    return {"status": "ok", "classification": "observed", **result, **metadata,
            "note": "Observed source response, not independently verified fact. Search snippets are not full documents."}


def _limit_worker_memory():
    """Hard process memory ceiling, including PDF decompression, on supported host platforms."""
    import os
    limit = 512 * 1024 * 1024
    if os.name != "nt":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        return
    import ctypes
    from ctypes import wintypes
    size = ctypes.c_size_t

    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("min_working", size), ("max_working", size),
                    ("active", wintypes.DWORD), ("affinity", size),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6), ("process_memory", size),
                    ("job_memory", size), ("peak_process", size), ("peak_job", size)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.process_memory = limit
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise OSError("Unable to enforce research worker memory limit")
    # Retain the job handle until process exit; the OS releases it then.


def main():
    try:
        _limit_worker_memory()
        result = execute_tool(json.load(sys.stdin))
    except Exception as exc:
        result = {"status": "unknown", "classification": "unknown", "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
