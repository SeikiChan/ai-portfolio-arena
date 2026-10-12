import json
from datetime import datetime, timedelta, timezone

import pytest

from ai_portfolios import execution
from ai_portfolios.common import read, write

TIME = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
CONFIG = {"universe": ["MSFT", "NVDA", "AAPL"], "max_positions": 10, "max_weight": 0.2, "max_invested": 0.95}


def quote(bid=99.9, ask=100.1, at=None):
    return {"bp": bid, "ap": ask, "t": (at or TIME).isoformat()}


def account(equity="10000", cash="10000", status="ACTIVE", identity="acct-1"):
    return {"id": identity, "account_number": "PA12345678", "status": status, "equity": equity, "cash": cash}


def decision(positions=(("MSFT", 0.15),)):
    return {"overview": "o", "cash_reason": "c", "evidence_gaps": [], "macro_claims": [], "memory_claims": [],
            "injection_flags": [], "positions": [{"symbol": s, "target_weight": w} for s, w in positions]}


def test_plan_converts_weights_to_whole_share_limit_orders():
    proposal = execution.plan(decision(), account(), [], {"MSFT": quote()}, {"MSFT": 100.0}, CONFIG, TIME)
    leg = proposal["orders"][0]
    assert leg["side"] == "buy" and leg["symbol"] == "MSFT"
    # 15% of 10,000 = 1,500 at a 100.30 limit -> 14 whole shares, never 14.95
    assert leg["qty"] == 14 and leg["limit_price"] == pytest.approx(100.3)
    assert float(leg["qty"]) == int(leg["qty"])


def test_limit_price_sits_past_the_touch_on_each_side():
    metrics = execution.quote_metrics("MSFT", quote(bid=100.0, ask=101.0), 100.0, TIME)
    assert execution.limit_for("buy", metrics) > metrics["ask"]
    assert execution.limit_for("sell", metrics) < metrics["bid"]


def test_exit_sells_the_whole_position_and_never_more():
    positions = [{"symbol": "MSFT", "qty": "14"}]
    proposal = execution.plan(decision(positions=(("MSFT", 0.0),)), account(), positions,
                              {"MSFT": quote()}, {"MSFT": 100.0}, CONFIG, TIME)
    leg = proposal["orders"][0]
    assert leg["side"] == "sell" and leg["qty"] == 14


def test_a_tiny_exit_is_still_executed_and_never_exceeds_the_shares_held():
    positions = [{"symbol": "MSFT", "qty": "3"}]
    # 3 shares is 0.3% of equity, far below the drift minimum - but an exit is a decision, not drift.
    proposal = execution.plan(decision(positions=(("MSFT", 0.0),)), account(equity="100000"), positions,
                              {"MSFT": quote()}, {"MSFT": 100.0}, CONFIG, TIME)
    assert proposal["skipped"] == []
    assert proposal["orders"][0]["side"] == "sell" and proposal["orders"][0]["qty"] == 3


def test_a_tiny_rebalance_is_still_treated_as_drift():
    positions = [{"symbol": "MSFT", "qty": "3"}]
    # Target 700 against a 300 position: 3 whole shares are buyable, but 400 is under the 500 minimum.
    proposal = execution.plan(decision(positions=(("MSFT", 0.007),)), account(equity="100000"), positions,
                              {"MSFT": quote()}, {"MSFT": 100.0}, CONFIG, TIME)
    assert proposal["orders"] == [] and "漂移" in proposal["skipped"][0]["reason"]


def test_a_position_too_small_for_one_share_is_reported_separately():
    proposal = execution.plan(decision(positions=(("MSFT", 0.0005),)), account(), [],
                              {"MSFT": quote()}, {"MSFT": 100.0}, CONFIG, TIME)
    assert proposal["orders"] == [] and "不足一股" in proposal["skipped"][0]["reason"]


def test_short_position_in_the_account_stops_everything():
    with pytest.raises(ValueError, match="空头"):
        execution.plan(decision(), account(), [{"symbol": "MSFT", "qty": "-1"}],
                       {"MSFT": quote()}, {"MSFT": 100.0}, CONFIG, TIME)


def test_drift_below_the_minimum_is_not_traded():
    positions = [{"symbol": "MSFT", "qty": "14"}]
    # Already at target: the residual is dust and must not become an order.
    proposal = execution.plan(decision(positions=(("MSFT", 0.1403),)), account(), positions,
                              {"MSFT": quote()}, {"MSFT": 100.0}, CONFIG, TIME)
    assert proposal["orders"] == []
    assert proposal["skipped"][0]["symbol"] == "MSFT"


def test_sells_still_go_out_when_buys_cannot_be_afforded():
    positions = [{"symbol": "MSFT", "qty": "50"}]
    proposal = execution.plan(decision(positions=(("MSFT", 0.0), ("NVDA", 0.2))), account(cash="100"), positions,
                              {"MSFT": quote(), "NVDA": quote()}, {"MSFT": 100.0, "NVDA": 100.0}, CONFIG, TIME)
    assert [leg["side"] for leg in proposal["orders"]] == ["sell"]
    assert proposal["deferred"][0]["symbol"] == "NVDA"
    # The weight is never shrunk to fit the cash: that would be deciding for the model.
    assert proposal["deferred"][0]["target_weight"] == 0.2
    assert "现金不足" in proposal["deferred"][0]["reason"]


@pytest.mark.parametrize("positions,error", [
    ((("MSFT", 0.5),), "单股目标权重"),
    ((("MSFT", 0.2), ("NVDA", 0.2), ("AAPL", 0.2)), None),
    ((("GOOGL", 0.1),), "股票池之外"),
])
def test_limits_are_rechecked_against_live_equity_at_execution_time(positions, error):
    quotes = {s: quote() for s, _ in positions}
    closes = {s: 100.0 for s, _ in positions}
    if error:
        with pytest.raises(ValueError, match=error):
            execution.plan(decision(positions), account(), [], quotes, closes, CONFIG, TIME)
    else:
        assert execution.plan(decision(positions), account(), [], quotes, closes, CONFIG, TIME)["orders"]


def test_total_target_above_the_cap_rejects_the_whole_plan():
    positions = (("MSFT", 0.2), ("NVDA", 0.2), ("AAPL", 0.2))
    config = {**CONFIG, "max_invested": 0.5}
    with pytest.raises(ValueError, match="总仓位"):
        execution.plan(decision(positions), account(), [], {s: quote() for s, _ in positions},
                       {s: 100.0 for s, _ in positions}, config, TIME)


@pytest.mark.parametrize("bad", [
    quote(bid=0), quote(ask=0), quote(bid=101, ask=100), quote(bid=float("nan")),
    quote(at=TIME + timedelta(seconds=5)), quote(at=TIME - timedelta(seconds=300)),
])
def test_an_unusable_quote_stops_the_plan_rather_than_being_patched(bad):
    with pytest.raises(ValueError):
        execution.plan(decision(), account(), [], {"MSFT": bad}, {"MSFT": 100.0}, CONFIG, TIME)


def test_price_far_from_the_frozen_close_pauses_execution():
    with pytest.raises(ValueError, match="相差超过"):
        execution.plan(decision(), account(), [], {"MSFT": quote(bid=199.9, ask=200.1)}, {"MSFT": 100.0}, CONFIG, TIME)


@pytest.mark.parametrize("status", ["INACTIVE", "ACCOUNT_CLOSED"])
def test_account_not_active_stops_execution(status):
    with pytest.raises(ValueError, match="账户状态"):
        execution.plan(decision(), account(status=status), [], {"MSFT": quote()}, {"MSFT": 100.0}, CONFIG, TIME)


def test_client_order_id_is_stable_for_a_leg():
    first = execution.client_order_id("r1", "claude", "MSFT", "buy")
    assert first == execution.client_order_id("r1", "claude", "MSFT", "buy")
    assert first != execution.client_order_id("r1", "claude", "MSFT", "sell")
    assert first != execution.client_order_id("r2", "claude", "MSFT", "buy")
    assert first != execution.client_order_id("r1", "gpt", "MSFT", "buy")
    assert len(first) <= 128


class Response:
    def __init__(self, status_code, body):
        self.status_code, self._body = status_code, body

    def json(self):
        return self._body


LEG = {"symbol": "MSFT", "side": "buy", "qty": 14, "limit_price": 100.3}


def test_accepted_order_records_the_broker_id():
    posted = []

    def poster(url, json=None, headers=None, timeout=None, allow_redirects=None):
        posted.append((url, json))
        return Response(200, {"id": "o-1", "status": "accepted"})

    record = execution.submit(LEG, ("k", "s"), "r1", "claude", poster=poster)
    url, payload = posted[0]
    assert url == "https://paper-api.alpaca.markets/v2/orders"
    assert payload["type"] == "limit" and payload["time_in_force"] == "day" and payload["extended_hours"] is False
    assert payload["qty"] == "14" and payload["limit_price"] == "100.30"
    assert record["outcome"] == "accepted" and record["order_id"] == "o-1"


def test_resubmitting_the_same_leg_is_recorded_as_a_duplicate_not_a_failure():
    poster = lambda *a, **k: Response(422, {"message": "client_order_id must be unique"})
    record = execution.submit(LEG, ("k", "s"), "r1", "claude", poster=poster)
    assert record["outcome"] == "duplicate_ignored"


def test_a_real_rejection_is_recorded_as_rejected():
    poster = lambda *a, **k: Response(403, {"message": "insufficient buying power"})
    record = execution.submit(LEG, ("k", "s"), "r1", "claude", poster=poster)
    assert record["outcome"] == "rejected" and "buying power" in record["broker_message"]


def test_submission_refuses_a_non_paper_host(monkeypatch):
    monkeypatch.setattr(execution, "BASE", "https://api.alpaca.markets")
    with pytest.raises(ValueError, match="Paper 域名"):
        execution.submit(LEG, ("k", "s"), "r1", "claude", poster=lambda *a, **k: Response(200, {}))


@pytest.fixture
def round_dir(tmp_path, monkeypatch):
    # Mirrors what engines.run writes: one engine owns one provider and one account, the round is
    # completed, the packet is fresh, and the team's account binding sits beside the rounds tree.
    root = tmp_path / "team"
    directory = root / "rounds" / "20261012T150000000000Z"
    write(directory / "round.json", {"id": "20261012T150000000000Z", "packet_id": "p1",
          "engine_id": "claude_engine", "providers": ["claude"], "status": "completed",
          "packet_frozen_at": (TIME - timedelta(hours=1)).isoformat()})
    write(directory / "packet.json", {"features": [{"symbol": "MSFT", "last_close": 100.0}]})
    write(directory / "claude_status.json", {"status": "validated", "decision": decision(), "instruction_sha256": "x"})
    write(root / "account_binding.json", {"account_id": "acct-1"})
    monkeypatch.setattr(execution, "paper_credentials", lambda only=None: {only: ("k", "s")})
    monkeypatch.setattr(execution, "market_data_keys", lambda: {"APCA_API_KEY_ID": "k", "APCA_API_SECRET_KEY": "s"})
    return directory


def record_file(round_dir):
    return round_dir.parents[1] / "executions" / round_dir.name / "execution.json"


def getter_for(acct=None, positions=(), is_open=True, quotes=None, orders=()):
    def getter(url, headers=None, params=None, timeout=None, allow_redirects=None):
        if "/v2/clock" in url:
            return Response(200, {"is_open": is_open, "next_open": "2026-10-13T13:30:00Z"})
        if "quotes/latest" in url:
            return Response(200, {"quotes": quotes if quotes is not None else {"MSFT": quote()}})
        if url.endswith("/v2/account"):
            return Response(200, acct or account())
        if url.endswith("/v2/positions"):
            return Response(200, list(positions))
        if url.endswith("/v2/orders"):
            return Response(200, list(orders))
        raise AssertionError("unexpected GET " + url)
    return getter


def test_dry_run_sends_nothing_to_the_broker(round_dir):
    def poster(*args, **kwargs):
        raise AssertionError("dry run must not post")

    record = execution.execute("claude", round_dir, CONFIG, submit_orders=False,
                               getter=getter_for(), poster=poster, at=TIME)
    assert record["mode"] == "dry_run" and record["orders_submitted"] is False and record["results"] == []
    assert record["plan"]["orders"][0]["symbol"] == "MSFT"
    assert read(record_file(round_dir))["orders_submitted"] is False


def test_submitting_writes_an_append_only_audit_record(round_dir):
    posted = []

    def poster(url, json=None, headers=None, timeout=None, allow_redirects=None):
        posted.append(json)
        return Response(200, {"id": "o-1", "status": "accepted"})

    record = execution.execute("claude", round_dir, CONFIG, submit_orders=True,
                               getter=getter_for(), poster=poster, at=TIME)
    assert len(posted) == 1 and record["orders_submitted"] is True
    assert record["broker"] == "https://paper-api.alpaca.markets"
    saved = record_file(round_dir)
    assert read(saved)["results"][0]["outcome"] == "accepted"
    with pytest.raises(FileExistsError):
        write(saved, {}, exclusive=True)


def test_closed_market_blocks_submission_but_not_a_dry_run(round_dir):
    with pytest.raises(ValueError, match="常规交易时段"):
        execution.execute("claude", round_dir, CONFIG, submit_orders=True,
                          getter=getter_for(is_open=False), poster=lambda *a, **k: Response(200, {}), at=TIME)
    assert execution.execute("claude", round_dir, CONFIG, submit_orders=False,
                             getter=getter_for(is_open=False), at=TIME)["mode"] == "dry_run"


def test_only_a_validated_decision_can_be_executed(round_dir):
    write(round_dir / "claude_status.json", {"status": "failed", "error": "boom"})
    with pytest.raises(ValueError, match="通过校验"):
        execution.execute("claude", round_dir, CONFIG, getter=getter_for(), at=TIME)


def test_account_identity_must_match_the_pinned_binding(round_dir):
    with pytest.raises(ValueError, match="账户身份与该团队绑定不符"):
        execution.execute("claude", round_dir, CONFIG,
                          getter=getter_for(acct=account(identity="someone-else")), at=TIME)


def test_a_missing_quote_blocks_submission(round_dir):
    with pytest.raises(ValueError, match="缺少报价"):
        execution.execute("claude", round_dir, CONFIG, submit_orders=True,
                          getter=getter_for(quotes={}), poster=lambda *a, **k: Response(200, {}), at=TIME)


def test_without_a_tradable_quote_a_dry_run_shows_indicative_intent(round_dir):
    record = execution.execute("claude", round_dir, CONFIG, submit_orders=False,
                               getter=getter_for(quotes={}), at=TIME)
    assert record["plan"]["tradable"] is False
    # Priced off the frozen close purely to show intent; it must never be submittable.
    assert record["plan"]["orders"][0]["limit_price"] == pytest.approx(100.0)
    assert record["orders_submitted"] is False


def test_an_indicative_plan_can_never_be_submitted(round_dir, monkeypatch):
    monkeypatch.setattr(execution, "session_open", lambda *a, **k: {"is_open": True})
    monkeypatch.setattr(execution, "plan", lambda *a, **k: {"tradable": False, "orders": [], "equity": 1.0,
                                                            "cash": 1.0, "deferred": [], "skipped": [], "no_op": []})
    with pytest.raises(ValueError, match="指示性计划不可提交"):
        execution.execute("claude", round_dir, CONFIG, submit_orders=True,
                          getter=getter_for(), poster=lambda *a, **k: Response(200, {}), at=TIME)


def test_a_watch_idea_with_no_position_is_a_no_op_and_is_never_quoted(round_dir):
    write(round_dir / "claude_status.json", {"status": "validated", "instruction_sha256": "x",
          "decision": decision(positions=(("MSFT", 0.15), ("NVDA", 0.0), ("AAPL", 0.0)))})
    asked = []

    def getter(url, headers=None, params=None, timeout=None, allow_redirects=None):
        if "quotes/latest" in url:
            asked.append(params["symbols"])
            return Response(200, {"quotes": {"MSFT": quote()}})
        return getter_for()(url, headers=headers, params=params, timeout=timeout, allow_redirects=allow_redirects)

    record = execution.execute("claude", round_dir, CONFIG, submit_orders=False, getter=getter, at=TIME)
    # Only MSFT could need an order, so only MSFT is quoted: a watch idea cannot block the plan.
    assert asked == ["MSFT"]
    assert record["plan"]["no_op"] == ["AAPL", "NVDA"]
    assert [leg["symbol"] for leg in record["plan"]["orders"]] == ["MSFT"]


def test_execution_never_calls_a_cancel_or_close_endpoint(round_dir):
    seen = []

    def poster(url, **kwargs):
        seen.append(url)
        return Response(200, {"id": "o-1", "status": "accepted"})

    execution.execute("claude", round_dir, CONFIG, submit_orders=True, getter=getter_for(), poster=poster, at=TIME)
    assert seen == ["https://paper-api.alpaca.markets/v2/orders"]
    source = (__import__("pathlib").Path(execution.__file__)).read_text(encoding="utf-8")
    for forbidden in ("requests.delete", "requests.patch", "/positions/", "close_all", ":cancel"):
        assert forbidden not in source


def test_a_stale_packet_cannot_be_executed(round_dir):
    # A decision made against evidence from two days ago is not a decision about today's market.
    write(round_dir / "round.json", {**read(round_dir / "round.json"),
          "packet_frozen_at": (TIME - timedelta(hours=25)).isoformat()})
    with pytest.raises(ValueError, match="已过期或时间在未来"):
        execution.execute("claude", round_dir, CONFIG, getter=getter_for(), at=TIME)


def test_an_open_order_on_a_target_symbol_blocks_the_round(round_dir):
    # An unfilled DAY limit still counts as exposure; stacking a second order would double it.
    open_order = [{"symbol": "MSFT", "status": "new", "qty": "5"}]
    with pytest.raises(ValueError, match="未完成委托"):
        execution.execute("claude", round_dir, CONFIG, getter=getter_for(orders=open_order), at=TIME)


def test_an_open_order_on_an_unrelated_symbol_does_not_block(round_dir):
    unrelated = [{"symbol": "AAPL", "status": "new", "qty": "5"}]
    assert execution.execute("claude", round_dir, CONFIG, getter=getter_for(orders=unrelated),
                             at=TIME)["mode"] == "dry_run"


@pytest.mark.parametrize("engine_id,providers", [
    ("codex_engine", ["claude"]), ("claude_engine", ["gpt"]), ("claude_engine", ["claude", "gpt"]),
])
def test_an_engine_can_only_trade_its_own_account(round_dir, engine_id, providers):
    # Deliberate: codex_engine must never reach the Claude account, and one round is one provider.
    write(round_dir / "round.json", {**read(round_dir / "round.json"),
          "engine_id": engine_id, "providers": providers})
    with pytest.raises(ValueError, match="归属不一致"):
        execution.execute("claude", round_dir, CONFIG, getter=getter_for(), at=TIME)


def test_an_incomplete_round_cannot_be_executed(round_dir):
    write(round_dir / "round.json", {**read(round_dir / "round.json"), "status": "partial_failure"})
    with pytest.raises(ValueError, match="归属不一致"):
        execution.execute("claude", round_dir, CONFIG, getter=getter_for(), at=TIME)


def test_sub_second_clock_skew_is_waited_out_not_clamped():
    """A quote that just arrived can be timestamped a shade ahead of a local clock running behind.

    Measured on the first live trading day: NVDA -0.172s, WMT -0.170s, which rejected both teams'
    entire plans. Waiting for the instant to pass is honest; zeroing the age would not be.
    """
    slept = []
    ahead = {"MSFT": quote(at=TIME + timedelta(milliseconds=200))}
    settled = execution.settle_quotes(ahead, TIME, sleep=slept.append)
    assert slept and 0.2 < slept[0] < 1.0
    assert settled >= TIME


def test_a_quote_already_in_the_past_is_not_waited_on():
    slept = []
    assert execution.settle_quotes({"MSFT": quote(at=TIME - timedelta(seconds=5))}, TIME, sleep=slept.append) == TIME
    assert slept == []


def test_a_clock_genuinely_wrong_stops_the_round(monkeypatch):
    # Beyond a couple of seconds this is a broken machine clock, and waiting cannot fix it.
    far = {"MSFT": quote(at=TIME + timedelta(seconds=30))}
    with pytest.raises(ValueError, match="时钟偏差"):
        execution.settle_quotes(far, TIME, sleep=lambda s: None)


def test_settling_runs_before_the_plan_is_priced(round_dir, monkeypatch):
    # The whole plan used to be rejected when any one symbol looked like it came from the future.
    calls = []
    monkeypatch.setattr(execution, "settle_quotes", lambda q, at, sleep=None: calls.append(at) or at)
    execution.execute("claude", round_dir, CONFIG, submit_orders=False, getter=getter_for(), at=TIME)
    assert calls == [TIME]
