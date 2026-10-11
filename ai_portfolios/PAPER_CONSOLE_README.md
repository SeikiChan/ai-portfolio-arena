# Claude / Codex shared Paper output page

The shared read-only page is `http://127.0.0.1:8790/`. It reads each team's sanitized
`exports/dashboard.json` and does not start engines or submit broker orders. Keep it local; do not expose it
to the internet. Start it with `powershell -ExecutionPolicy Bypass -File .\scripts\Start-SharedOutput.ps1`.

## Automated Paper cycle

The cycle runs once per open US market day at 10:00 America/New_York. It freezes one shared market packet,
then starts `claude_engine` and `codex_engine` separately with that packet. Claude may access only the
Claude Paper credentials and Codex may access only the Codex Paper credentials. A validated target portfolio
may create whole-share DAY limit orders in that team's Paper account.

To run one cycle manually:

```powershell
.\.venv\Scripts\python.exe -m ai_portfolios.auto_trader --once
```

To install the daily Windows task:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Install-AutoTraderTask.ps1
```

The scheduled task starts at 7:00 local time and the program waits until 10:00 America/New_York. It runs
only while the configured Windows user is logged in. On a closed market it records `market_closed` and
does not collect data or send orders. Its daily state is under `reports/auto_trader/`.

To run just one team manually, use `python -m engines.run --engine claude_engine` or
`python -m engines.run --engine codex_engine`. These submit by default after validation. Add `--no-submit`
to build a dry-run plan. A team cannot rerun the same packet to select a different result.

## Order and account safeguards

- Broker URL is hard-coded to `https://paper-api.alpaca.markets`; no live endpoint is supported.
- The model has no network, file, shell, account credential, or broker tools.
- The executor uses limit orders and the configured universe/exposure/position limits. It never shrinks a
  target allocation to fit cash. An unaffordable buy is deferred.
- Wrong account binding, partial account snapshot, invalid or stale quote, closed market, existing open
  order, invalid model output, or engine failure stops new orders for that team.
- API timeout during order submission is recorded as an unknown outcome because Alpaca may have accepted it.
- Round artifacts are immutable. Order logs are separate append-only files under each engine's `executions`
  directory.

Paper trading is simulated and does not establish that either strategy will work with real money. The
shared web page displays the latest broker-reported Paper account state and order status, not inferred fills.
