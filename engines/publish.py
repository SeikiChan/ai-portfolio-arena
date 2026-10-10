"""Publication boundary. Emits the dashboard.json that the shared console reads.

Codex independently built the same arrangement from its side: a read-only publication boundary
(`ai_portfolios/team_exports.py` in the alpaca-live workspace) whose comment reads "Never import code
or state from another team's workspace", backed by a versioned `exports/dashboard.json`. That boundary
is better than having the console import this package, so this module conforms to it rather than
competing with it: nothing here is imported by the console, and the console imports nothing from here.

What crosses the boundary is a single validated JSON file. Account cash, account numbers and broker
records never enter it — the export carries research content and audit hashes only.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ai_portfolios.common import ROOT, read, stamp, write

from . import contract

SCHEMA_VERSION = 1
TEAM_ID = "claude-independent"
DEFAULT_EXPORT = ROOT / "exports" / "dashboard.json"
POSITION_FIELDS = ("symbol", "action", "target_weight", "horizon_days", "confidence")


def provider_entry(directory, provider):
    status_file = directory / f"{provider}_status.json"
    status = read(status_file) if status_file.exists() else {}
    decision = status.get("decision") or {}
    return {"provider": provider, "status": status.get("status", "unknown"),
            "model_requested": status.get("model_requested"), "error": status.get("error"),
            "completed_at": status.get("completed_at"), "overview": decision.get("overview"),
            "cash_reason": decision.get("cash_reason"), "evidence_gaps": decision.get("evidence_gaps", []),
            "macro_claims": [c.get("claim") for c in decision.get("macro_claims", [])],
            "injection_flags": decision.get("injection_flags", []),
            "positions": [{k: p.get(k) for k in POSITION_FIELDS} for p in decision.get("positions", [])]}


def round_entry(directory):
    """A round only reaches the export after passing the contract. Unpublishable rounds are reported."""
    summary = contract.validate_round(directory)
    job = read(directory / "round.json")
    return {**summary, "at": job.get("completed_at") or job.get("started_at"),
            "engine_author": job.get("engine_author"), "reasoning_effort": job.get("reasoning_effort"),
            "mandate_sha256": job.get("mandate_sha256"), "orders_submitted": False,
            "models": [provider_entry(directory, provider) for provider in summary["providers"]]}


def build(engine_id="claude_engine", team_id=TEAM_ID, limit=60):
    rounds, rejected = [], []
    root = contract.result_root(engine_id)
    for directory in sorted((d for d in root.glob("*") if d.is_dir()), reverse=True)[:limit]:
        try:
            rounds.append(round_entry(directory))
        except (ValueError, KeyError, OSError) as exc:
            # A round that breaches the contract is never silently dropped: a missing round and an
            # invalid round must not look the same to whoever reads the dashboard.
            rejected.append({"round_id": directory.name, "reason": str(exc)[:200]})
    coverage = contract.cell_coverage([read(d / "round.json") for d in root.glob("*")
                                       if (d / "round.json").exists()])
    return {"schema_version": SCHEMA_VERSION, "team_id": team_id, "engine_id": engine_id,
            "status": "available" if rounds else "unavailable",
            "published_at": stamp(), "rounds": rounds, "rejected_rounds": rejected,
            "design_coverage": coverage, "orders_submitted": False,
            "note": "研究建议与审计哈希；不含账户资金、账号或券商记录。建议不等于成交，收益未经验证。",
            "classification": {"model_decisions": "assumption", "audit_hashes": "observed",
                               "forward_returns": "unknown"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", default="claude_engine")
    parser.add_argument("--team-id", default=TEAM_ID)
    parser.add_argument("--out", default=str(DEFAULT_EXPORT))
    arguments = parser.parse_args()
    export = build(arguments.engine, arguments.team_id)
    write(Path(arguments.out), export)
    print(f"{export['status']}: {len(export['rounds'])} rounds, {len(export['rejected_rounds'])} rejected "
          f"-> {arguments.out}")
    if not export["design_coverage"]["separable"]:
        print("WARNING design not separable; empty cells: " + ", ".join(export["design_coverage"]["empty_cells"]))


if __name__ == "__main__":
    main()
