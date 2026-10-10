from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = Path(__file__).resolve().parent
DATA = ROOT / "reports" / "ai_portfolios"


def now():
    return datetime.now(timezone.utc)


def stamp():
    return now().isoformat()


def parse_time(value):
    # Alpaca emits nanoseconds; Python 3.10 accepts at most six fractional digits.
    # Truncate (never round into the future) for conservative time comparisons.
    normalized = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
    result = datetime.fromisoformat(normalized)
    if result.tzinfo is None:
        raise ValueError("Timestamp must have a timezone")
    return result


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if exclusive:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
    else:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(encoded, encoding="utf-8")
        os.replace(temp, path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def env_values():
    # Parse only the local data credentials; never execute or print .env content.
    values = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if match:
                values[match[1]] = match[2].strip("\"'")
    return {k: os.environ.get(k) or values.get(k) for k in
            ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "FMP_API_KEY")}


def external_reference():
    """Optional pointer at an outside audit ledger (e.g. another repo's truth ledger).

    This project used to live inside a real-money trading repo and recorded which ledger snapshot was
    current for every round. Standalone, that ledger may simply not exist: record `unknown` honestly
    rather than crashing, and never substitute a guess for a missing audit anchor.
    """
    configured = read(APP / "config.json").get("external_truth_ledger")
    if not configured:
        return {"status": "unknown", "reason": "No external audit ledger configured for this repository"}
    ledger = Path(configured)
    if not ledger.is_absolute():
        ledger = ROOT / ledger
    if not ledger.exists():
        return {"status": "unknown", "reason": "Configured external ledger is not reachable from this machine"}
    exclusions = set()
    correction = ledger.parent / "snapshot_corrections.jsonl"
    if correction.exists():
        for line in correction.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item.get("status") in {"invalid", "incomplete", "superseded"}:
                exclusions.add(item["snapshot_id"])
    entries = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    for entry in reversed(entries):
        if entry["snapshot_id"] not in exclusions:
            manifest = ledger.parent.parent.parent / entry["manifest"]
            if not manifest.exists():
                continue
            if read(manifest)["snapshot_id"] != entry["snapshot_id"]:
                raise ValueError("External ledger identity mismatch")
            return {"status": "linked", "snapshot_id": entry["snapshot_id"], "manifest": str(manifest), "sha256": digest(manifest)}
    return {"status": "unknown", "reason": "External ledger has no usable snapshot"}


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
