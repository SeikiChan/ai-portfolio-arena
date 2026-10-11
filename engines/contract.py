"""Executable form of ENGINE_CONTRACT.md.

Neutral layer: it belongs to no engine, and no single author may change it alone. Everything here is
deterministic so that who wrote an engine cannot affect whether that engine's output is accepted.
"""
from __future__ import annotations

import re
from pathlib import Path

from ai_portfolios.common import ROOT, digest, read

PROVIDERS = ("claude", "gpt")
ENGINE_PROVIDER = {"claude_engine": "claude", "codex_engine": "gpt"}
ENGINE_ID = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
ENGINES_ROOT = ROOT / "reports" / "engines"
ROUND_STATUSES = {"completed", "partial_failure", "failed"}
ROUND_REQUIRED = {"id", "engine_id", "engine_version", "engine_author", "providers", "environment",
                  "packet_id", "packet_sha256", "packet_frozen_at", "instruction_sha256", "mandate_sha256", "strict_comparison",
                  "reasoning_effort", "account_snapshot", "orders_submitted", "status", "started_at", "completed_at"}


def result_root(engine_id):
    if not isinstance(engine_id, str) or not ENGINE_ID.match(engine_id):
        raise ValueError("Engine id must be lowercase letters, digits and underscores")
    return ENGINES_ROOT / engine_id / "rounds"


def check_engine(engine):
    """Interface conformance. Does not run the engine; a slot that cannot be imported simply has none."""
    for attribute in ("engine_id", "author", "version"):
        value = getattr(engine, attribute, None)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Engine is missing {attribute}")
    if not ENGINE_ID.match(engine.engine_id):
        raise ValueError("Engine id must be lowercase letters, digits and underscores")
    for method in ("instruction", "schema", "decide"):
        if not callable(getattr(engine, method, None)):
            raise ValueError(f"Engine is missing callable {method}")
    instruction = engine.instruction("alpaca_paper")
    if not isinstance(instruction, str) or len(instruction.strip()) < 200:
        raise ValueError("Engine instruction must be substantial text")
    schema = engine.schema()
    if not isinstance(schema, dict) or schema.get("type") != "object" or not schema.get("required"):
        raise ValueError("Engine schema must be an object schema with required fields")
    return {"engine_id": engine.engine_id, "author": engine.author, "version": engine.version,
            "instruction_chars": len(instruction), "schema_fields": sorted(schema["required"])}


def validate_round(directory):
    """Contract conformance of a finished round directory. Raises ValueError with the first breach."""
    directory = Path(directory)
    job_file = directory / "round.json"
    if not job_file.exists():
        raise ValueError("Round is missing round.json")
    job = read(job_file)
    missing = ROUND_REQUIRED - set(job)
    if missing:
        raise ValueError("Round is missing required fields: " + ",".join(sorted(missing)))
    if not ENGINE_ID.match(str(job["engine_id"])):
        raise ValueError("Round declares an invalid engine id")
    if not isinstance(job["packet_id"], str) or not job["packet_id"] or not isinstance(job["packet_frozen_at"], str):
        raise ValueError("Round must record the shared packet identity and frozen time")
    if job["status"] not in ROUND_STATUSES:
        raise ValueError("Round declares an unknown status")
    # Strictly False, never merely falsy: this is the one claim the whole project rests on.
    if job["orders_submitted"] is not False:
        raise ValueError("Round must declare orders_submitted false")
    providers = job["providers"]
    if not isinstance(providers, list) or not providers or len(set(providers)) != len(providers) \
            or any(p not in PROVIDERS for p in providers):
        raise ValueError("Round must declare a non-empty set of known providers")
    expected_provider = ENGINE_PROVIDER.get(job["engine_id"])
    if expected_provider and providers != [expected_provider]:
        raise ValueError(f"{job['engine_id']} must run only its own provider: {expected_provider}")
    account = job["account_snapshot"]
    if not isinstance(account, dict) or account.get("provider") not in providers or \
            not re.fullmatch(r"[0-9a-f]{64}", str(account.get("account_id_sha256", ""))):
        raise ValueError("Round must identify its isolated Paper account snapshot")
    account_dir = directory / "account_snapshot"
    if not account_dir.is_dir() or not isinstance(account.get("files"), dict):
        raise ValueError("Round is missing its Paper account snapshot manifest")
    actual_account_files = {path.name for path in account_dir.iterdir() if path.is_file()}
    if set(account["files"]) != actual_account_files:
        raise ValueError("Paper account snapshot files do not match the manifest")
    for name, expected in account["files"].items():
        if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", str(expected)) or \
                digest(account_dir / name) != expected:
            raise ValueError("Paper account snapshot file hash mismatch: " + str(name))
    packet = directory / "packet.json"
    if not packet.exists():
        raise ValueError("Round is missing its frozen packet.json")
    if digest(packet) != job["packet_sha256"]:
        raise ValueError("Round packet does not match its declared packet_sha256")
    if not (directory / "receipt.json").exists():
        raise ValueError("Round is missing receipt.json")
    receipt = read(directory / "receipt.json")
    for key in ("round_id", "engine_id", "packet_id", "packet_sha256", "files"):
        if key not in receipt:
            raise ValueError("Receipt is missing " + key)
    if (receipt["round_id"] != job["id"] or receipt["engine_id"] != job["engine_id"] or
            receipt["packet_id"] != job["packet_id"] or receipt["packet_sha256"] != job["packet_sha256"]):
        raise ValueError("Receipt identity does not match the round")
    if not isinstance(receipt["files"], dict):
        raise ValueError("Receipt file hashes must be an object")
    actual_files = {path.name for path in directory.iterdir() if path.is_file() and path.name != "receipt.json"}
    if set(receipt["files"]) != actual_files:
        raise ValueError("Receipt must hash every round file exactly once")
    for name, expected in receipt["files"].items():
        if not isinstance(name, str) or Path(name).name != name or name == "receipt.json" or \
                not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Receipt contains an invalid file name or SHA-256")
        path = directory / name
        if not path.is_file() or digest(path) != expected:
            raise ValueError("Receipt file hash mismatch: " + str(name))
    mandates = job["mandate_sha256"]
    if not isinstance(mandates, dict) or set(mandates) != set(providers):
        raise ValueError("Round must record one mandate hash per provider")
    for provider in providers:
        for suffix in ("_prompt.txt", "_status.json"):
            if not (directory / (provider + suffix)).exists():
                raise ValueError(f"Round is missing {provider}{suffix}")
    strict = job["strict_comparison"]
    if strict is not True and strict is not False:
        raise ValueError("strict_comparison must be a boolean")
    # A round where the mandates differ compares prompts, not models, and may never be called strict.
    if strict and (set(providers) != set(PROVIDERS) or len(set(mandates.values())) != 1):
        raise ValueError("A strict round needs both providers under a byte-identical mandate")
    statuses = {}
    for provider in providers:
        status = read(directory / f"{provider}_status.json")
        if status.get("provider") != provider or status.get("orders_submitted") is not False:
            raise ValueError("Provider status identity or order boundary is invalid")
        statuses[provider] = status.get("status")
    if job["status"] == "completed" and any(value != "validated" for value in statuses.values()):
        raise ValueError("A completed round contains a failed provider")
    if job["status"] == "partial_failure" and ("validated" not in statuses.values() or all(value == "validated" for value in statuses.values())):
        raise ValueError("partial_failure must preserve both a success and a failure")
    if job["status"] == "failed" and "validated" in statuses.values():
        raise ValueError("A round with a validated provider must preserve that partial success")
    return {"round_id": job["id"], "engine_id": job["engine_id"], "engine_version": job["engine_version"],
            "providers": sorted(providers), "status": job["status"], "strict_comparison": strict,
            "packet_sha256": job["packet_sha256"], "instruction_sha256": job["instruction_sha256"]}


def cell_coverage(rounds):
    """Fill state of the engine x model design. Empty off-diagonal cells mean author and model are fused.

    See ENGINE_CONTRACT.md section 1: an engine that only ever runs its own author's model makes the
    model effect, the engine effect and the author bias mathematically inseparable.
    """
    cells = {}
    for job in rounds:
        for provider in job.get("providers", []):
            cells[(job.get("engine_id"), provider)] = cells.get((job.get("engine_id"), provider), 0) + 1
    engines = sorted({engine for engine, _ in cells})
    empty = [(engine, provider) for engine in engines for provider in PROVIDERS if not cells.get((engine, provider))]
    return {"cells": {f"{engine}/{provider}": count for (engine, provider), count in sorted(cells.items())},
            "engines": engines, "empty_cells": [f"{e}/{p}" for e, p in empty],
            "separable": len(engines) > 1 and not empty}
