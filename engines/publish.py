"""Publication boundary. Emits the dashboard.json that the shared console reads.

Codex independently built the same arrangement from its side: a read-only publication boundary
(`ai_portfolios/team_exports.py` in the alpaca-live workspace) whose comment reads "Never import code
or state from another team's workspace", backed by a versioned `exports/dashboard.json`. That boundary
is better than having the console import this package, so this module conforms to it rather than
competing with it: nothing here is imported by the console, and the console imports nothing from here.

What crosses the boundary is a single validated JSON file. It may carry the team's own sanitized
Paper account marks and broker order status; keys and full account IDs never cross the boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
from pathlib import Path

from ai_portfolios.common import ROOT, read, stamp, write

from . import contract

SCHEMA_VERSION = 1
TEAM_IDS = {"claude_engine": "claude-independent", "codex_engine": "gpt-independent"}
DEFAULT_EXPORTS = {
    "claude_engine": ROOT / "exports" / "dashboard.json",
    "codex_engine": Path(os.environ.get("AI_ARENA_CODEX_EXPORT") or
                           Path.home() / "alpaca-gpt-research" / "gpt_research" / "exports" / "dashboard.json"),
}
POSITION_FIELDS = ("symbol", "action", "target_weight", "horizon_days", "confidence")
# Each trading engine runs one provider and publishes one team entry per round.
ELISION = (chr(10) * 2 + "[INPUT 段已省略：它包含本轮账户现金与持仓。完整正文的 sha256 见 full_prompt_sha256，原文留在本团队工作区的轮次档案里。]")


def branch_name():
    git_dir = ROOT / ".git"
    if git_dir.is_file():
        pointer = git_dir.read_text(encoding="utf-8").strip()
        if not pointer.startswith("gitdir: "):
            return None
        git_dir = (ROOT / pointer.removeprefix("gitdir: ")).resolve()
    head = git_dir / "HEAD"
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
    # Both engine prompt delimiters must remove private decision input and raw research material.
    head = full
    for delimiter in ("\nINPUT:\n", "\n输入：\n"):
        head = head.partition(delimiter)[0]
    return head + ELISION, hashlib.sha256(full.encode("utf-8")).hexdigest(), len(full)


def account_mark(directory, provider, job):
    """One observed Paper account mark at the round's recorded snapshot time."""
    path = directory / "account_snapshot" / f"{provider}_account.json"
    if not path.exists():
        return None
    account = read(path)
    try:
        equity = float(account["equity"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(equity) or equity <= 0:
        return None
    return {"round_id": job["id"], "experiment_id": job.get("experiment_id"),
            "at": job.get("account_snapshot_id"), "equity": equity,
            "classification": "observed: Alpaca Paper account snapshot",
            "source_file": f"account_snapshot/{provider}_account.json"}


def research_trace(directory, provider):
    """Publish model research actions and bounded public tool excerpts, never prompt inputs."""
    turns = []
    for path in sorted(directory.glob(f"research_*_{provider}_response.json")):
        turn = read(path)
        turns.append({"kind": "model", "step": path.stem.split("_")[1],
                      "done": turn.get("done"), "actions": turn.get("actions", [])})
    for path in sorted(directory.glob("research_*_request.json")):
        result_path = path.with_name(path.name.replace("_request.json", "_result.json"))
        request = read(path)
        result = read(result_path) if result_path.exists() else {}
        excerpt = result.get("text") or ""
        turns.append({"kind": "tool", "step": path.stem.split("_")[1],
                      "action": request.get("action", {}), "status": result.get("status", "unknown"),
                      "classification": result.get("classification", "unknown"),
                      "text": excerpt[:4000], "excerpt_truncated": len(excerpt) > 4000,
                      "url": result.get("url"), "source_sha256": result.get("source_sha256")})
    return sorted(turns, key=lambda row: (int(row["step"]), row["kind"] != "model"))


def decision_evidence(directory, decision):
    packet_path = directory / "decision_packet.json"
    if not packet_path.exists():
        packet_path = directory / "packet.json"
    packet = read(packet_path)
    used = {identity for position in decision.get("positions", [])
            for identity in position.get("evidence_ids", [])}
    rows = []
    for item in packet.get("evidence", []):
        if item.get("id") not in used:
            continue
        rows.append({key: item.get(key) for key in ("id", "kind", "classification", "as_of", "url", "headline")})
    facts = {item["id"]: item.get("data", {}) for item in packet.get("evidence", []) if item.get("id") in used}
    return rows, facts


def provider_entry(directory, provider, summary, job):
    status_file = directory / f"{provider}_status.json"
    status = read(status_file) if status_file.exists() else {}
    decision = status.get("decision") or {}
    answer = directory / f"{provider}_response.txt"
    elided, prompt_sha, prompt_chars = prompt_text(directory, provider)
    execution_file = directory.parents[1] / "executions" / summary["round_id"] / "execution.json"
    execution = read(execution_file) if execution_file.exists() else {}
    broker_orders = execution.get("results", [])
    account = execution.get("account_observed", {})
    mark = account_mark(directory, provider, job)
    evidence, facts = decision_evidence(directory, decision)
    detailed_positions = []
    for position in decision.get("positions", []):
        references = []
        for reference in position.get("fact_refs", []):
            identity, _, field = reference.partition("#")
            references.append({"ref": reference, "value": facts.get(identity, {}).get(field)})
        detailed_positions.append({key: position.get(key) for key in
                                   ("symbol", "action", "target_weight", "thesis", "changes_vs_last",
                                    "invalidation", "horizon_days", "confidence", "evidence_ids", "evidence_quotes")}
                                  | {"facts": references})
    return {"id": f"{summary['round_id']}:{provider}", "round_id": summary["round_id"], "provider": provider,
            "experiment_id": job.get("experiment_id"), "research_enabled": job.get("research_enabled", False),
            "decision_packet_sha256": job.get("decision_packet_sha256"),
            "status": status.get("status", "unknown"), "completed_at": status.get("completed_at") or summary.get("at"),
            "model_requested": status.get("model_requested"), "error": status.get("error"),
            "user_prompt": (job.get("user_prompts") or {}).get(provider, "本轮未记录指令全文；见 mandate_sha256。"),
            "full_prompt": elided, "full_prompt_sha256": prompt_sha, "full_prompt_chars": prompt_chars,
            "response": answer.read_text(encoding="utf-8") if answer.exists() else None,
            "engine_id": summary["engine_id"], "engine_version": summary["engine_version"],
            "engine_author": job.get("engine_author"), "reasoning_effort": job.get("reasoning_effort"),
            "packet_sha256": summary["packet_sha256"], "instruction_sha256": summary["instruction_sha256"],
            "mandate_sha256": (job.get("mandate_sha256") or {}).get(provider),
            "strict_comparison": summary["strict_comparison"],
            "execution_mode": execution.get("mode", "not_executed"),
            "execution_error": execution.get("execution_error"),
            "orders_submitted": any(o.get("outcome") in {"accepted", "duplicate_ignored", "submission_unknown"} for o in broker_orders),
            "account_observed": account,
            "equity_mark": mark, "research_trace": research_trace(directory, provider),
            "decision_details": detailed_positions, "decision_evidence": evidence,
            "broker_orders": broker_orders,
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


def build(engine_id="claude_engine", team_id=None, limit=20):
    team_id = team_id or TEAM_IDS.get(engine_id)
    if not team_id:
        raise ValueError("No publication team id is registered for this engine")
    rounds, rejected = [], []
    root = contract.result_root(engine_id)
    for directory in sorted((d for d in root.glob("*") if d.is_dir()), reverse=True)[:limit]:
        try:
            rounds.extend(round_entries(directory))
        except (ValueError, KeyError, OSError) as exc:
            # A round that breaches the contract is never silently dropped: a missing round and an
            # invalid round must not look the same to whoever reads the dashboard.
            rejected.append({"round_id": directory.name, "reason": str(exc)[:200]})
    history = sorted((row["equity_mark"] for row in rounds if row.get("equity_mark")), key=lambda row: row["at"] or "")
    return {"schema_version": SCHEMA_VERSION, "team_id": team_id, "engine_id": engine_id,
            "branch": branch_name(), "workspace": str(ROOT), "execution_mode": "alpaca_paper_auto",
            "status": "available" if rounds else ("initialized_no_runs" if not rejected else "unavailable"),
            "published_at": stamp(), "rounds": rounds, "equity_history": history, "rejected_rounds": rejected,
            "orders_submitted": any(round_.get("orders_submitted") for round_ in rounds),
            "note": "独立引擎各自连接自己的 Alpaca Paper 账户；页面只读共享发布文件。Paper 委托与成交不代表真实资金结果，收益未经验证。",
            "classification": {"model_decisions": "assumption", "audit_hashes": "observed",
                               "forward_returns": "unknown"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", default="claude_engine")
    parser.add_argument("--team-id")
    parser.add_argument("--limit", type=int, default=20, help="Rounds to publish; prompts make the file large")
    parser.add_argument("--out")
    arguments = parser.parse_args()
    export = build(arguments.engine, arguments.team_id, arguments.limit)
    output = Path(arguments.out) if arguments.out else DEFAULT_EXPORTS[arguments.engine]
    write(output, export)
    print(f"{export['status']}: {len(export['rounds'])} rounds, {len(export['rejected_rounds'])} rejected "
          f"-> {output}")


if __name__ == "__main__":
    main()
