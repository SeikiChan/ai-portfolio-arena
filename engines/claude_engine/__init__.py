"""Claude-authored research engine.

Owns only the layer where authorship legitimately differs: the fixed instruction, the output schema,
the deterministic validation, and the regression bench. Evidence collection, account reading and the
console are shared infrastructure and are not re-implemented here.

This is Claude's independent trading team. It runs Claude against the Claude Paper account only.
"""
from pathlib import Path

from ai_portfolios import models
from ai_portfolios.common import read, APP

MANDATE = Path(__file__).with_name("mandate.md")


class ClaudeEngine:
    engine_id = "claude_engine"
    author = "Claude"
    version = models.INSTRUCTION_VERSION

    def default_mandate(self):
        """The mandate this engine ships with.

        The console writes the live mandate to prompts.json and the daily coordinator reads it from
        there, so this is a default and a seed, never the authority: a mandate edited in the console
        must not be silently reverted by a file in the repository. It exists so the two copies can be
        compared, because on 2026-10-13 editing this file alone changed nothing about the next run.
        """
        return MANDATE.read_text(encoding="utf-8").strip()

    def instruction(self, portfolio_mode="alpaca_paper"):
        return models.research_instruction(portfolio_mode)

    def schema(self):
        return models.SCHEMA

    def decide(self, provider, packet, previous, mandate, directory, config=None):
        if provider != "claude":
            raise ValueError("claude_engine may run only the Claude provider")
        return models.run_model(provider, packet, previous, config or read(APP / "config.json"),
                                directory, mandate, "alpaca_paper")


ENGINE = ClaudeEngine()
