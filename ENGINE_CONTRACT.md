# Claude / Codex Paper Trading Contract

This contract supersedes the earlier research-only and 2×2 comparison design. The product goal is two
independent automated Paper trading teams, not a Claude-vs-GPT prompt benchmark.

```
                   One frozen market packet
                     /              \
                    /                \
    Claude engine + Claude CLI     Codex engine + Codex CLI
       Claude Paper account           Codex Paper account
                    \                /
                     \              /
                    Sanitized JSON exports
                            |
                 One shared, read-only page
```

## 1. Isolation and shared boundaries

- `claude_engine` may invoke only the Claude provider and may load only Claude Paper credentials.
- `codex_engine` may invoke only the GPT/Codex provider and may load only Codex Paper credentials.
- The two engines never import or read each other's prompt, decisions, account data, or execution logs.
- Both engines start with the exact same frozen market packet from the neutral packet store. Optional
  model-directed public research is isolated per provider and saved separately; it never overwrites the
  shared packet. Research-enabled teams do not see identical final evidence or identical decision times.
- Each team publishes a sanitized `exports/dashboard.json`. The shared console reads those files only; it never imports engine code and never sends orders.
- A Paper account identity is pinned to its engine. A changed account identity stops the run and requires a new experiment.

## 2. Model and execution boundary

The model returns a complete target portfolio. It has no broker tools and cannot choose an order quantity,
order type, or execution price. After deterministic validation, `ai_portfolios.execution` reads that engine's
own Paper account and creates whole-share DAY limit orders. The only broker write endpoint is
`POST https://paper-api.alpaca.markets/v2/orders`. There is no live endpoint, market order, short sale,
leverage, cancel, replace, or manual close endpoint. A target weight of zero can create a sell-to-zero limit
order. Broker-reported fills, not model prose, define the resulting holdings.

Every run is Paper-only. Missing/incomplete account data, account-binding mismatch, invalid/stale quotes,
market-closed clock, an open order for a target symbol, or a model/schema failure prevents new orders.
The executor enforces the configured stock universe, position count, per-stock weight and total invested
limits; it does not shrink a model target to fit cash. Cash-insufficient buys are deferred. A network timeout
while submitting is recorded as an unknown outcome because the broker may have accepted the request.

`engines.run` submits Paper orders by default after a completed, validated decision. `--no-submit` builds
only a dry-run plan. The daily coordinator freezes one packet at 10:00 America/New_York on an open session,
then starts each engine once with that packet. An engine/packet pair cannot be rerun to pick a better answer.
Starting the coordinator or its scheduled task is an operational action; it is never started by the web page.

## 3. Round artifact

Each round is stored under `reports/engines/<engine_id>/rounds/<round_id>/` and is append-only. It contains
the frozen packet, one provider prompt/status/response, a hashed Paper account snapshot, `round.json`, and
`receipt.json`. The round describes the model decision and always has `orders_submitted: false`; execution
is stored separately under `reports/engines/<engine_id>/executions/<round_id>/execution.json` so the sealed
round receipt remains immutable.

Required `round.json` fields include:

```json
{
  "id": "...", "engine_id": "claude_engine", "engine_version": "...",
  "engine_author": "Claude", "providers": ["claude"],
  "environment": "alpaca_paper", "account_snapshot_id": "...",
  "account_snapshot": {"provider": "claude", "account_id_sha256": "...", "files": {}},
  "packet_id": "...", "packet_sha256": "...", "packet_frozen_at": "...",
  "strict_comparison": false, "orders_submitted": false,
  "status": "completed | failed", "started_at": "...", "completed_at": "..."
}
```

The corresponding Codex round uses `engine_id: codex_engine` and `providers: ["gpt"]`. One-provider
rounds are deliberately not labeled a strict model comparison and do not enter the old 2×2 rankings.
The execution artifact records account suffix only, sanitized observed positions/orders, order requests,
broker results, packet identity, and source hashes. It contains no API credentials or full account ID.

## 4. Experiment identity and evidence discipline

Changing the engine prompt/schema/validator, limits, stock universe, execution rules, or cadence starts a new
experiment. Never join old and new performance curves. Current order/execution behavior starts a new
Paper experiment; old research-only rounds remain historical and are not executed retroactively.

Every report records `round_id`, `packet_id`, packet hash, account snapshot identity, files, observation
window and calculation source. Use `observed`, `derived`, `assumption`, and `unknown` accurately. Missing
data is never replaced with a guess or a prior close presented as a fill.

User-authorized public research (`public-research-v1`) is enabled by `config.research.enabled` and the
engine IDs in `config.research.engines` (currently Codex only, preserving Claude's independent work). A bounded
provider-specific planning loop can request public web/news/paper searches, HTTPS document/PDF reads,
and finite arithmetic. The host dispatches those requests; the CLI itself still has no native tools.
Research receives public packet evidence, not account snapshots, and only invokes its own provider.
No local-file, arbitrary-shell, authenticated-site, broker, cancellation or order tool is exposed.
The network worker pins public DNS addresses, checks every redirect, enforces byte/time limits and
receives no API credentials. Source failures are `unknown`, not synthetic evidence.

The original `packet.json` stays immutable. Research requests, responses and raw downloaded bytes are
append-only top-level round artifacts sealed by the existing receipt. `decision_packet.json` freezes
the expanded evidence with its own timestamp and hash; its `base_frozen_at` preserves market-data time.
Retrieved content is not automatically verified fact, retrieval time is not publication time, and papers
are not company financial statements. Existing position evidence/schema requirements still apply.
The final decision CLI remains tool-free and is validated against precisely this expanded packet.

`experiment_id` hashes research implementation, policy, model/config, engine instruction and mandate.
Changing any of these starts another research experiment. Previous decisions are loaded only from the
same experiment; actual existing Paper holdings are retained. Do not splice performance across IDs or
treat pre-existing account gains as new-experiment gains. Research planning failures fail the round;
individual unavailable sources are archived and reported as unknown. Limits are resource budgets, not
trading targets: six planning turns, twelve calls, ten minutes of research, forty seconds per tool.
Historical prompt benchmarks must replay saved `decision_packet.json` without live research. Do not
attach today's web research to historical inputs and call that a backtest.

## 5. Publication

The shared page reads only the two versioned exports. Exports may include each team's own sanitized Paper
account marks, positions and order/fill statuses so the user can compare them in one place. They must never
include secrets or full account identifiers. The shared page is local-only and read-only; it cannot initiate
model runs or broker mutations for the independent teams.
