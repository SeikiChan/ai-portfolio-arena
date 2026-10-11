"""Claude-authored research engine.

Owns only the layer where authorship legitimately differs: the fixed instruction, the output schema,
the deterministic validation, and the regression bench. Evidence collection, account reading and the
console are shared infrastructure and are not re-implemented here.

This is Claude's independent trading team. It runs Claude against the Claude Paper account only.
"""
from ai_portfolios import models
from ai_portfolios.common import read, APP


class ClaudeEngine:
    engine_id = "claude_engine"
    author = "Claude"
    version = models.INSTRUCTION_VERSION

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
