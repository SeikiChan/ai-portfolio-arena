"""Mechanical scoring of declared invalidation conditions; this does not estimate portfolio returns."""
from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from ai_portfolios.common import ROOT, digest, parse_time, read, stamp, write

from . import contract

STORE = Path(__import__("os").environ.get("AI_ARENA_PACKET_STORE") or Path.home() / "ai-arena-packets")
PACKET_ROUNDS = STORE / "rounds"
OUTPUT = ROOT / "reports" / "adjudication"


def _decision_rows(engine_root=None):
    root = Path(engine_root) if engine_root else contract.ENGINES_ROOT
    for round_dir in sorted(root.glob("*/rounds/*")):
        if not round_dir.is_dir() or not (round_dir / "round.json").is_file():
            continue
        job = read(round_dir / "round.json")
        if job.get("orders_submitted") is not False:
            continue
        for provider in job.get("providers", []):
            status_file = round_dir / f"{provider}_status.json"
            if not status_file.exists():
                continue
            status = read(status_file)
            if status.get("status") != "validated":
                continue
            decision = status.get("decision")
            if isinstance(decision, dict):
                yield round_dir, job, provider, decision


def _packets(packet_root=None):
    root = Path(packet_root) if packet_root else PACKET_ROUNDS
    rows = []
    for path in root.glob("*/packet.json"):
        try:
            packet = read(path)
            pointer = read(path.parent / "store_manifest.json")
            actual_hash = digest(path)
            if actual_hash != pointer.get("packet_sha256"):
                continue
            rows.append({"packet_id": path.parent.name, "packet_sha256": actual_hash,
                         "frozen_at": pointer.get("frozen_at"), "packet": packet})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(rows, key=lambda row: parse_time(row["frozen_at"]))


def _threshold(rule, base_packet, symbol):
    ref = rule.get("reference", "")
    if rule.get("kind") not in {"price_below", "price_above"}:
        return None
    try:
        return float(ref)
    except (TypeError, ValueError):
        match = re.fullmatch(r"price:([A-Z.]+)#last_close", ref)
        if not match or match.group(1) != symbol:
            return None
        for row in base_packet.get("evidence", []):
            if row.get("id") == f"price:{symbol}":
                value = row.get("data", {}).get("last_close")
                return float(value) if isinstance(value, (int, float)) else None
    return None


def _trigger(rule, symbol, packet, threshold):
    kind = rule.get("kind")
    evidence = packet.get("evidence", [])
    if kind in {"price_below", "price_above"}:
        price = next((row.get("data", {}).get("last_close") for row in evidence
                      if row.get("id") == f"price:{symbol}"), None)
        if not isinstance(price, (int, float)) or threshold is None:
            return None, None
        hit = price < threshold if kind == "price_below" else price > threshold
        row = next((row for row in evidence if row.get("id") == f"price:{symbol}"), {})
        return hit, row.get("id")
    if kind == "news_event":
        terms = rule.get("reference", "").removeprefix("news:contains:").casefold().split("|")
        for row in evidence:
            if row.get("kind") != "news" or row.get("symbol") != symbol:
                continue
            text = (str(row.get("headline", "")) + " " + str(row.get("summary", ""))).casefold()
            if any(term and term in text for term in terms):
                return True, row.get("id")
        return False, None
    if kind == "financial_event":
        match = re.fullmatch(r"financial:(revenue|grossProfit|operatingIncome|netIncome|eps):(lt|gt):(-?\d+(?:\.\d+)?)", rule.get("reference", ""))
        if not match:
            return None, None
        field, operator, target = match.group(1), match.group(2), float(match.group(3))
        for row in evidence:
            if row.get("kind") != "financials" or row.get("symbol") != symbol:
                continue
            value = row.get("data", {}).get(field)
            if isinstance(value, (int, float)):
                return (value < target if operator == "lt" else value > target), row.get("id")
        return False, None
    return None, None


def evaluate(job, provider, decision, packets):
    completed = parse_time(job["completed_at"])
    base = next((item for item in packets if item["packet_id"] == job.get("packet_id") and item["packet_sha256"] == job.get("packet_sha256")), None)
    if base is None:
        return []
    results = []
    for position in decision.get("positions", []):
        rule = position.get("invalidation") or {}
        symbol = position.get("symbol")
        horizon = int(position.get("horizon_days", 0))
        due = completed + timedelta(days=horizon)
        threshold = _threshold(rule, base["packet"], symbol)
        observed = []
        scorable_count = 0
        # Evidence is only eligible after the decision; dates are taken from the packet's frozen clock.
        for item in packets:
            at = parse_time(item["frozen_at"])
            if at <= completed:
                continue
            if at > due:
                break
            evidence = item["packet"].get("evidence", [])
            errors = item["packet"].get("errors", [])
            if rule.get("kind") in {"price_below", "price_above"}:
                if any(row.get("id") == f"price:{symbol}" and isinstance(row.get("data", {}).get("last_close"), (int, float)) for row in evidence):
                    scorable_count += 1
            elif rule.get("kind") == "news_event":
                if not any(error.get("source") == f"news_{symbol}" for error in errors if isinstance(error, dict)):
                    scorable_count += 1
            elif rule.get("kind") == "financial_event":
                if any(row.get("kind") == "financials" and row.get("symbol") == symbol and
                       parse_time(row.get("as_of", completed.isoformat())) > completed for row in evidence):
                    scorable_count += 1
            hit, evidence_id = _trigger(rule, symbol, item["packet"], threshold)
            if hit is True:
                observed.append({"packet_id": item["packet_id"], "at": item["frozen_at"], "evidence_id": evidence_id})
                break
        latest = max((parse_time(item["frozen_at"]) for item in packets if parse_time(item["frozen_at"]) > completed), default=None)
        if observed:
            outcome = "triggered"
            event = 1.0
        elif latest is not None and latest >= due and rule.get("kind") == "time":
            outcome, event = "review_due", None
        elif latest is not None and latest >= due and scorable_count > 0:
            outcome = "not_triggered_by_horizon"
            event = 0.0
        elif rule.get("kind") not in {"price_below", "price_above", "news_event", "financial_event", "time"} or \
                (rule.get("kind") in {"price_below", "price_above"} and threshold is None):
            outcome, event = "unscorable", None
        elif latest is not None and latest >= due:
            outcome, event = "unscorable", None
        else:
            outcome, event = "censored", None
        confidence = position.get("confidence")
        # The field means P(invalidation does not occur during the horizon).
        brier = (float(confidence) - (1.0 - event)) ** 2 if event is not None and isinstance(confidence, (int, float)) else None
        results.append({"round_id": job["id"], "engine_id": job["engine_id"], "engine_version": job["engine_version"],
                        "provider": provider, "packet_id": job["packet_id"], "packet_sha256": job["packet_sha256"],
                        "decision_completed_at": job["completed_at"], "symbol": symbol, "horizon_days": horizon,
                        "horizon_due_at": due.isoformat(), "invalidation": rule, "confidence_no_trigger": confidence,
                        "outcome": outcome, "trigger_evidence": observed[0] if observed else None,
                        "brier_score": brier, "classification": "derived_from_frozen_evidence"})
    return results


def run(packet_root=None, engine_root=None, output=None):
    packets = _packets(packet_root)
    outcomes = []
    round_rows = list(_decision_rows(engine_root))
    for round_dir, job, provider, decision in round_rows:
        outcomes.extend(evaluate(job, provider, decision, packets))
    groups = {}
    for row in outcomes:
        key = (row["engine_id"], row["provider"])
        group = groups.setdefault(key, {"n_scored": 0, "n_pending_or_unscorable": 0, "brier_sum": 0.0})
        if row["brier_score"] is None:
            group["n_pending_or_unscorable"] += 1
        else:
            group["n_scored"] += 1
            group["brier_sum"] += row["brier_score"]
    summary = {f"{engine}/{provider}": {**value,
                "mean_brier": value["brier_sum"] / value["n_scored"] if value["n_scored"] else None}
               for (engine, provider), value in sorted(groups.items())}
    body = {"created_at": stamp(), "outcomes": outcomes, "summary": summary,
            "coverage": contract.cell_coverage(list({job["id"]: job for _, job, _, _ in round_rows}.values())),
            "input_rounds": sorted({row["round_id"] for row in outcomes}),
            "input_packets": sorted({(row["packet_id"], row["packet_sha256"]) for row in outcomes}),
            "source_code": {"engines/adjudication.py": digest(Path(__file__))},
            "method": "Scores only declared invalidation conditions. Brier target is confidence that the condition will not trigger within the stated calendar-day horizon. Time-only rules are reported as review_due and are not scored; censored and unscorable observations are excluded.",
            "classification": {"outcomes": "derived_from_frozen_evidence", "investment_skill": "unknown", "portfolio_returns": "not_scored"}}
    target = Path(output) if output else OUTPUT / (now_id() + ".json")
    write(target, body, exclusive=True)
    return target, body


def now_id():
    from ai_portfolios.common import now
    return now().strftime("%Y%m%dT%H%M%S%fZ")


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet-root")
    parser.add_argument("--engine-root")
    parser.add_argument("--out")
    args = parser.parse_args()
    target, body = run(args.packet_root, args.engine_root, args.out)
    print(json.dumps({"file": str(target), "outcomes": len(body["outcomes"]), "summary": body["summary"],
                      "coverage": body["coverage"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
