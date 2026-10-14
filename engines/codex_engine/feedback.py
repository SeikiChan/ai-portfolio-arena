"""Host-computed process feedback for this Codex experiment, never return rewards."""
from pathlib import Path
from ai_portfolios.common import digest, parse_time, read
from engines import contract

POLICY = "process-feedback-v1"


def score_result(result):
    if result.get("status") == "validated":
        return {"reward": 1, "code": "accepted"}
    if result.get("status") == "failed" and result.get("failure_kind") == "invalid_output":
        code = "minimum_invested" if "minimum invested weight violation" in result.get("error", "") else "invalid_output"
        return {"reward": -1, "code": code}
    return {"reward": None, "code": "unscored_failure"}


def history(rounds_root, experiment_id, before, limit=20):
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Feedback history limit must be 1..20")
    records = []
    for path in sorted(Path(rounds_root).glob("*/receipt.json"), reverse=True):
        directory = path.parent
        job = read(directory / "round.json")
        if job.get("engine_id") != "codex_engine" or job.get("providers") != ["gpt"]:
            raise ValueError("Foreign team feedback refused")
        if job.get("experiment_id") != experiment_id:
            continue
        if parse_time(job["completed_at"]) >= parse_time(before):
            continue
        contract.validate_round(directory)
        result = read(directory / "gpt_status.json")
        records.append({"round_id": directory.name, "completed_at": job["completed_at"],
                        "input_file": f"{directory.name}/gpt_status.json",
                        "source_sha256": digest(directory / "gpt_status.json"), **score_result(result)})
        if len(records) == limit:
            break
    records.reverse()
    values = [r["reward"] for r in records if r["reward"] is not None]
    return {"policy": POLICY, "classification": "derived", "reward_policy_classification": "assumption",
            "experiment_id": experiment_id, "records": records, "row_count": len(records),
            "observation_start": records[0]["completed_at"] if records else None,
            "observation_end": records[-1]["completed_at"] if records else None,
            "calculation_script": "engines/codex_engine/feedback.py", "calculation_sha256": digest(__file__),
            "scored_count": len(values), "reward_sum": sum(values) if values else None,
            "investment_skill": "unknown", "return_reward": None}
