"""Repeat an engine/provider on one saved packet and report process-quality metrics only."""
from __future__ import annotations

import argparse
import itertools
import json
import re
import shutil
import statistics
from copy import deepcopy
from pathlib import Path

from ai_portfolios.common import APP, ROOT, digest, now, read, stamp, write

from . import ENGINE

BENCH_ROOT = ROOT / "reports" / "engines" / "codex_engine" / "bench"
MACRO_TERMS = ("标普", "标准普尔", "纳斯达克", "道琼斯", "道指", "美债", "国债", "美联储", "联储",
               "加息", "降息", "CPI", "通胀", "非农", "失业率", "美元指数", "VIX", "油价", "原油", "十年期", "10年期")


def _squeeze(value):
    return re.sub(r"\s+", "", str(value))


def _metrics(decision, packet):
    evidence = {row["id"]: row for row in packet.get("evidence", [])}
    quotes = list(decision.get("macro_claims", [])) + [q for p in decision.get("positions", []) for q in p.get("evidence_quotes", [])]
    faithful = sum(1 for row in quotes if row.get("evidence_id") in evidence and
                   _squeeze(row.get("quote", "")) in _squeeze(json.dumps(evidence[row["evidence_id"]], ensure_ascii=False, sort_keys=True)))
    prose = [decision.get("overview", "")] + [str(p.get("thesis", "")) + " " + str(p.get("changes_vs_last", "")) for p in decision.get("positions", [])]
    macro = [str(c.get("claim", "")) for c in decision.get("macro_claims", [])]
    positive = [p for p in decision.get("positions", []) if isinstance(p.get("target_weight"), (int, float)) and p["target_weight"] > 0]
    return {"positive_positions": len(positive), "positive_symbols": sorted(p["symbol"] for p in positive),
            "total_weight": round(sum(p["target_weight"] for p in decision.get("positions", [])), 4),
            "quote_count": len(quotes), "quote_faithful": faithful,
            "unattributed_macro_terms": [term for term in MACRO_TERMS if any(term in item for item in prose) and not any(term in claim for claim in macro)],
            "machine_checkable_invalidations": sum(1 for p in positive if p.get("invalidation", {}).get("kind") in {"price_below", "price_above", "news_event", "financial_event"})}


def _summary(rows):
    parsed = [row for row in rows if row.get("metrics") is not None]
    sets = [set(row["metrics"]["positive_symbols"]) for row in parsed]
    jaccard = [len(a & b) / len(a | b) if a | b else 1.0 for a, b in itertools.combinations(sets, 2)]
    qn = sum(row["metrics"]["quote_count"] for row in parsed)
    qf = sum(row["metrics"]["quote_faithful"] for row in parsed)
    positions = sum(row["metrics"]["positive_positions"] for row in parsed)
    return {"runs": len(rows), "parsed": len(parsed),
            "schema_pass_rate": sum(row["status"] == "validated" for row in rows) / len(rows),
            "quote_faithfulness": qf / qn if qn else None,
            "mean_positive_positions": statistics.fmean(row["metrics"]["positive_positions"] for row in parsed) if parsed else None,
            "mean_total_weight": statistics.fmean(row["metrics"]["total_weight"] for row in parsed) if parsed else None,
            "unattributed_macro_runs": sum(bool(row["metrics"]["unattributed_macro_terms"]) for row in parsed),
            "machine_checkable_invalidation_ratio": sum(row["metrics"]["machine_checkable_invalidations"] for row in parsed) / positions if positions else None,
            "self_consistency_jaccard": statistics.fmean(jaccard) if jaccard else None,
            "failures": sorted({row["error"] for row in rows if row.get("error")})}


def run(packet_path, provider, repeat, mandate, output_root=None):
    packet_path = Path(packet_path)
    packet = read(packet_path)
    config = read(APP / "config.json")
    packet_hash = digest(packet_path)
    if packet_path.parent.joinpath("store_manifest.json").exists():
        manifest = read(packet_path.parent / "store_manifest.json")
        if manifest.get("packet_sha256") != packet_hash:
            raise ValueError("Saved packet does not match the shared store hash")
        packet_id = manifest.get("packet_id")
    else:
        packet_id = packet.get("snapshot_id")
    bench_id = now().strftime("%Y%m%dT%H%M%S%fZ")
    directory = (Path(output_root) if output_root else BENCH_ROOT) / bench_id
    directory.mkdir(parents=True, exist_ok=False)
    copied_packet = directory / "packet.json"
    shutil.copyfile(packet_path, copied_packet)
    previous = {"cash": config["initial_cash"], "holdings": {}, "last_decision": None,
                "classification": "assumption: synthetic empty book for prompt regression"}
    write(directory / "bench.json", {"bench_id": bench_id, "engine_id": ENGINE.engine_id, "engine_version": ENGINE.version,
          "engine_author": ENGINE.author, "provider": provider, "repeat": repeat, "packet_id": packet_id,
          "packet_sha256": packet_hash, "mandate_sha256": __import__("hashlib").sha256(mandate.encode("utf-8")).hexdigest(),
          "started_at": stamp(), "orders_submitted": False, "scope": "process metrics only; no return claim"}, exclusive=True)
    rows = []
    for index in range(repeat):
        child = directory / f"rep{index:02d}"
        child.mkdir()
        result = ENGINE.decide(provider, packet, deepcopy(previous), mandate, child, config)
        row = {"repeat": index, "status": result.get("status"), "error": result.get("error"), "metrics": None}
        if isinstance(result.get("decision"), dict):
            row["metrics"] = _metrics(result["decision"], packet)
        rows.append(row)
    summary = _summary(rows)
    write(directory / "summary.json", {"bench_id": bench_id, "completed_at": stamp(), "summary": summary, "repeats": rows}, exclusive=True)
    return directory, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", required=True, help="Path to a frozen packet.json; never collects data")
    parser.add_argument("--provider", choices=("claude", "gpt"), required=True)
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--mandate-file")
    parser.add_argument("--out-root")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 100:
        parser.error("--repeat must be between 1 and 100")
    mandate = Path(args.mandate_file).read_text(encoding="utf-8") if args.mandate_file else "请说明证据、推断、风险和可检验的失效条件。"
    directory, summary = run(args.packet, args.provider, args.repeat, mandate, args.out_root)
    print(json.dumps({"directory": str(directory), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
