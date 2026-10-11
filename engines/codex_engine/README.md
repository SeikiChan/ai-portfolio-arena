# Codex Paper trading engine

This engine owns the Codex team's instruction, schema, validation, and provider invocation. It only invokes
the GPT/Codex CLI and loads only the Codex Paper account. It does not import or read Claude's engine,
portfolio, credentials, or execution history.

The neutral runner gives it the shared frozen packet and the current state of its own Paper account. A
validated target portfolio is handed to the separate Paper executor. It writes immutable rounds under
`reports/engines/codex_engine/rounds/` and sanitized broker execution records under
`reports/engines/codex_engine/executions/`.

One default automatic Paper run:

```powershell
python -m engines.run --engine codex_engine --packet-id <shared-packet-id>
```

The run submits validated DAY limit orders by default. To inspect a dry-run plan without submitting:

```powershell
python -m engines.run --engine codex_engine --packet-id <shared-packet-id> --no-submit
```

The daily coordinator freezes one packet and calls both isolated teams at 10:00 America/New_York:

```powershell
python -m ai_portfolios.auto_trader --once
```

The shared website reads sanitized team exports; it cannot start this engine or submit orders. There is no
live-account mode.
