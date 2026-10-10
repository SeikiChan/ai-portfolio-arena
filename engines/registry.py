"""Discover engines without importing one engine from another."""
from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from . import contract

PACKAGE = Path(__file__).resolve().parent


def declared():
    """Every engine folder present, implemented or not. A slot with only a README is still declared."""
    return sorted(item.name for item in PACKAGE.iterdir()
                  if item.is_dir() and not item.name.startswith(("_", ".")) and contract.ENGINE_ID.match(item.name))


def loaded():
    """Engines that import cleanly and pass interface conformance, keyed by engine_id.

    An engine that fails to import is reported, never silently skipped: a broken engine must not be
    mistaken for an engine that simply has not run yet.
    """
    engines, problems = {}, {}
    for name in declared():
        if not (PACKAGE / name / "__init__.py").exists():
            problems[name] = "declared slot, not implemented"
            continue
        try:
            module = importlib.import_module(f"{__package__}.{name}")
            engine = getattr(module, "ENGINE")
            contract.check_engine(engine)
            if engine.engine_id != name:
                raise ValueError("Engine id does not match its folder name")
            engines[name] = engine
        except Exception as exc:
            problems[name] = f"{type(exc).__name__}: {exc}"[:200]
    return engines, problems


def get(engine_id):
    engines, problems = loaded()
    if engine_id not in engines:
        raise ValueError(f"Engine {engine_id} unavailable: {problems.get(engine_id, 'not declared')}")
    return engines[engine_id]


def result_roots():
    """(engine_id, rounds directory) for every declared engine, so the console can render them all."""
    return [(name, contract.result_root(name)) for name in declared()]
