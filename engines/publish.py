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
import hashlib
from pathlib import Path

from ai_portfolios.common import ROOT, read, stamp, write

from . import contract

SCHEMA_VERSION = 1
TEAM_ID = "claude-independent"
DEFAULT_EXPORT = ROOT / "exports" / "dashboard.json"
POSITION_FIELDS = ("symbol", "action", "target_weight", "horizon_days", "confidence")
# The shared viewer renders one entry per model, so a two-model round publishes two entries.
ELISION = (chr(10) * 2 + "[INPUT 段已省略：它包含本轮账户现金与持仓。完整正文的 sha256 见 full_prompt_sha256，原文留在本团队工作区的轮次档案里。]")


def branch_name():
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return None
    text = head.read_text(encoding="utf-8").strip()
    return text.split("refs/heads/", 1)[1] if "refs/heads/" in text else text[:12]


def prompt_text(directory, provider):
    """Prompt text minus the INPUT section: the export must never carry account cash or holdings."""
    path = directory / f"{provider}_prompt.txt"
    if not path.exists():
        return None, None, 0
    full = path.read_text(encoding="utf-8")
    head, _, _ = full.partition(chr(10) + "INPUT:" + chr(10))
    return head + ELISION, hashlib.sha256(full.encode("utf-8")).hexdigest(), len(full)


def provider_entry(directory, provider, summary, job):
    status_file = directory / f"{provider}_status.json"
    status = read(status_file) if status_file.exists() else {}
    decision = status.get("decision") or {}
    answer = directory / f"{provider}_response.txt"
    elided, prompt_sha, prompt_chars = prompt_text(directory, provider)
    return {"id": f"{summary['round_id']}:{provider}", "round_id": summary["round_id"], "provider": provider,
            "status": status.get("status", "unknown"), "completed_at": status.get("completed_at") or summary.get("at"),
            "model_requested": status.get("model_requested"), "error": status.get("error"),
            "user_prompt": (job.get("user_prompts") or {}).get(provider, "本轮未记录指令全文；见 mandate_sha256。"),
            "full_prompt": elided, "full_prompt_sha256": prompt_sha, "full_prompt_chars": prompt_chars,
            "response": answer.read_text(encoding="utf-8") if answer.exists() else None,
            "engine_id": summary["engine_id"], "engine_version": summary["engine_version"],
            "engine_author": job.get("engine_author"), "reasoning_effort": job.get("reasoning_effort"),
            "packet_sha256": summary["packet_sha256"], "instruction_sha256": summary["instruction_sha256"],
            "mandate_sha256": (job.get("mandate_sha256") or {}).get(provider),
            "strict_comparison": summary["strict_comparison"], "orders_submitted": False,
            "overview": decision.get("overview"), "cash_reason": decision.get("cash_reason"),
            "evidence_gaps": decision.get("evidence_gaps", []),
            "macro_claims": [c.get("claim") for c in decision.get("macro_claims", [])],
            "injection_flags": decision.get("injection_flags", []),
            "positions": [{k: p.get(k) for k in POSITION_FIELDS} for p in decision.get("positions", [])]}


def round_entries(directory):
    """A round only reaches the export after passing the contract. Unpublishable rounds are reported."""
    summary = contract.validate_round(directory)
    job = read(directory / "round.json")
    summary = {**summary, "at": job.get("completed_at") or job.get("started_at")}
    return [provider_entry(directory, provider, summary, job) for provider in summary["providers"]]


def build(engine_id="claude_engine", team_id=TEAM_ID, limit=20):
    rounds, rejected = [], []
    root = contract.result_root(engine_id)
    for directory in sorted((d for d in root.glob("*") if d.is_dir()), reverse=True)[:limit]:
        try:
            rounds.extend(round_entries(directory))
        except (ValueError, KeyError, OSError) as exc:
            # A round that breaches the contract is never silently dropped: a missing round and an
            # invalid round must not look the same to whoever reads the dashboard.
            rejected.append({"round_id": directory.name, "reason": str(exc)[:200]})
    coverage = contract.cell_coverage([read(d / "round.json") for d in root.glob("*")
                                       if (d / "round.json").exists()])
    return {"schema_version": SCHEMA_VERSION, "team_id": team_id, "engine_id": engine_id,
            "branch": branch_name(), "workspace": str(ROOT), "execution_mode": "read_only_research",
            "status": "available" if rounds else ("initialized_no_runs" if not rejected else "unavailable"),
            "published_at": stamp(), "rounds": rounds, "rejected_rounds": rejected,
            "design_coverage": coverage, "orders_submitted": False,
            "note": "研究建议与审计哈希；不含账户资金、账号或券商记录。建议不等于成交，收益未经验证。",
            "classification": {"model_decisions": "assumption", "audit_hashes": "observed",
                               "forward_returns": "unknown"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", default="claude_engine")
    parser.add_argument("--team-id", default=TEAM_ID)
    parser.add_argument("--limit", type=int, default=20, help="Rounds to publish; prompts make the file large")
    parser.add_argument("--out", default=str(DEFAULT_EXPORT))
    arguments = parser.parse_args()
    export = build(arguments.engine, arguments.team_id, arguments.limit)
    write(Path(arguments.out), export)
    print(f"{export['status']}: {len(export['rounds'])} rounds, {len(export['rejected_rounds'])} rejected "
          f"-> {arguments.out}")
    if not export["design_coverage"]["separable"]:
        print("WARNING design not separable; empty cells: " + ", ".join(export["design_coverage"]["empty_cells"]))


if __name__ == "__main__":
    main()
