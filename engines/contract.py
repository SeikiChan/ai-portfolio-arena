"""Executable form of ENGINE_CONTRACT.md.

Neutral layer: it belongs to no engine, and no single author may change it alone. Everything here is
deterministic so that who wrote an engine cannot affect whether that engine's output is accepted.
"""
from __future__ import annotations

import re
from pathlib import Path

from ai_portfolios.common import ROOT, digest, read

PROVIDERS = ("claude", "gpt")
ENGINE_ID = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
ENGINES_ROOT = ROOT / "reports" / "engines"
ROUND_STATUSES = {"completed", "partial_failure", "failed"}
ROUND_REQUIRED = {"id", "engine_id", "engine_version", "engine_author", "providers", "environment",
                  "packet_sha256", "instruction_sha256", "mandate_sha256", "strict_comparison",
                  "reasoning_effort", "orders_submitted", "status", "started_at", "completed_at"}


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
    if job["status"] not in ROUND_STATUSES:
        raise ValueError("Round declares an unknown status")
    # Strictly False, never merely falsy: this is the one claim the whole project rests on.
    if job["orders_submitted"] is not False:
        raise ValueError("Round must declare orders_submitted false")
    providers = job["providers"]
    if not isinstance(providers, list) or not providers or len(set(providers)) != len(providers) \
            or any(p not in PROVIDERS for p in providers):
        raise ValueError("Round must declare a non-empty set of known providers")
    packet = directory / "packet.json"
    if not packet.exists():
        raise ValueError("Round is missing its frozen packet.json")
    if digest(packet) != job["packet_sha256"]:
        raise ValueError("Round packet does not match its declared packet_sha256")
    if not (directory / "receipt.json").exists():
        raise ValueError("Round is missing receipt.json")
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
    if strict and (len(providers) < 2 or len(set(mandates.values())) != 1):
        raise ValueError("A strict round needs both providers under a byte-identical mandate")
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
