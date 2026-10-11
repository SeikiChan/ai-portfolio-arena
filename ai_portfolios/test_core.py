from copy import deepcopy
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from ai_portfolios.book import fresh_quote, initial_book, record_equity, settle
from ai_portfolios.common import APP, parse_time, read
from ai_portfolios.models import quality_flags, validate
from ai_portfolios.prompt_bench import inject, score

REAL_CONFIG = read(APP / "config.json")
# Most cases here exercise one position at a time; the participation floor is a portfolio-level rule
# and gets its own tests below rather than masking every specific breach these cases are about.
CONFIG = {**REAL_CONFIG, "min_invested": 0}
TIME = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)


def test_alpaca_nanosecond_timestamp_on_python310():
    parsed = parse_time("2026-10-09T15:00:00.036793495Z")
    assert parsed.microsecond == 36793
    assert parsed.utcoffset() == timedelta(0)


def quote(time=TIME, bid=99.9, ask=100.1):
    return {"bp": bid, "ap": ask, "t": time.isoformat()}


def decision():
    return {"overview": "依据当前证据进行初始研究配置", "cash_reason": "保留现金等待新的可验证投资机会", "evidence_gaps": [],
            "macro_claims": [], "memory_claims": [], "injection_flags": [],
            "positions": [{"symbol": "AAPL", "action": "new", "target_weight": 0.2,
                           "thesis": "这是充分说明买入原因的研究假设", "changes_vs_last": "首次建仓，此前账户没有持仓",
                           "invalidation": {"kind": "price_below", "reference": "price:AAPL#mean_20d", "description": "收盘跌破 20 日均线则减仓或退出"},
                           "horizon_days": 20, "confidence": 0.6,
                           "evidence_ids": ["price:AAPL", "news:AAPL:1"], "fact_refs": ["price:AAPL#last_close"],
                           "evidence_quotes": [{"evidence_id": "news:AAPL:1", "quote": "季度交付量高于上一季"}]}]}


def packet():
    return {"frozen_at": TIME.isoformat(), "evidence": [
        {"id": "price:AAPL", "symbol": "AAPL", "kind": "derived_price", "as_of": (TIME - timedelta(days=1)).isoformat(),
         "data": {"last_close": 100.0, "mean_20d": 96.0, "mean_60d": 90.0, "return_20d": 0.05}},
        {"id": "news:AAPL:1", "symbol": "AAPL", "kind": "news", "as_of": TIME.isoformat(),
         "headline": "公司公布季度交付情况", "summary": "季度交付量高于上一季，公司称需求稳定。"}]}


def planned_book():
    book = initial_book(100000)
    book["pending"] = {"id": "first", "completed_at": (TIME - timedelta(minutes=2)).isoformat(), "decision": decision()}
    return book


def test_validate_positive_plan():
    assert validate(decision(), CONFIG, packet(), initial_book(100000))


@pytest.mark.parametrize("weight", [float("nan"), float("inf"), -0.01, 0.201, True])
def test_reject_bad_weight(weight):
    plan = decision()
    plan["positions"][0]["target_weight"] = weight
    with pytest.raises(ValueError):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_duplicate_symbol_rejected():
    plan = decision()
    plan["positions"].append(deepcopy(plan["positions"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_invented_reference_rejected():
    plan = decision()
    plan["positions"][0]["evidence_ids"].append("made_up")
    with pytest.raises(ValueError, match="Invented"):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_other_stock_news_cannot_support_trade():
    data = packet()
    data["evidence"][1]["symbol"] = "NVDA"
    with pytest.raises(ValueError, match="symbol-specific"):
        validate(decision(), CONFIG, data, initial_book(100000))


def test_omitted_holding_rejected():
    book = initial_book(100000)
    book["holdings"]["NVDA"] = {"shares": 1, "last_mark": 100}
    with pytest.raises(ValueError, match="omitted"):
        validate(decision(), CONFIG, packet(), book)


def test_fill_after_decision_and_only_once():
    original = planned_book()
    book, status = settle(original, {"AAPL": quote()}, TIME, CONFIG)
    assert status == "shadow_rebalance_recorded"
    assert original["holdings"] == {}
    assert book["cash"] == pytest.approx(80000)
    assert book["holdings"]["AAPL"]["shares"] == pytest.approx(20000 / 100.1)
    assert book["stress_cost"] == pytest.approx(10)
    again, status = settle(book, {"AAPL": quote()}, TIME, CONFIG)
    assert status == "no_pending_plan"
    assert again == book


@pytest.mark.parametrize("offset", [-180, 1])
def test_stale_or_future_quote_never_fills(offset):
    book = planned_book()
    updated, status = settle(book, {"AAPL": quote(TIME + timedelta(seconds=offset))}, TIME, CONFIG)
    assert updated == book
    assert status.startswith("pending:")


def test_predecision_quote_rejected_even_if_fresh():
    book = planned_book()
    book["pending"]["completed_at"] = (TIME - timedelta(seconds=10)).isoformat()
    updated, status = settle(book, {"AAPL": quote()}, TIME, CONFIG)
    assert updated == book
    assert "predates" in status


@pytest.mark.parametrize("q", [quote(bid=101, ask=100), quote(bid=float("nan")), quote(bid=0), quote(ask=120)])
def test_invalid_quotes_rejected(q):
    with pytest.raises(ValueError):
        fresh_quote("AAPL", {"AAPL": q}, TIME, CONFIG)


def test_outside_market_session_rejected():
    at = TIME.replace(hour=23)
    with pytest.raises(ValueError, match="regular session"):
        fresh_quote("AAPL", {"AAPL": quote(at)}, at, CONFIG)


def test_expiry_clears_without_fill():
    book = planned_book()
    book["pending"]["completed_at"] = (TIME - timedelta(days=2)).isoformat()
    updated, status = settle(book, {}, TIME, CONFIG)
    assert status == "plan_expired"
    assert updated["pending"] is None
    assert updated["cash"] == book["cash"]


def test_missing_one_quote_rejects_entire_rebalance():
    book = planned_book()
    book["holdings"]["NVDA"] = {"shares": 1, "last_mark": 100}
    updated, status = settle(book, {"AAPL": quote()}, TIME, CONFIG)
    assert updated == book
    assert "NVDA" in status


def test_liquidation_cash_and_spread_accounting():
    bought, _ = settle(planned_book(), {"AAPL": quote()}, TIME, CONFIG)
    plan = decision()
    plan["positions"][0]["target_weight"] = 0
    bought["pending"] = {"id": "sell", "completed_at": (TIME - timedelta(minutes=2)).isoformat(), "decision": plan}
    sold, status = settle(bought, {"AAPL": quote()}, TIME, CONFIG)
    assert status == "shadow_rebalance_recorded"
    assert sold["holdings"] == {}
    assert sold["cash"] == pytest.approx(80000 + 20000 / 100.1 * 99.9)


def test_missing_mark_never_invents_a_price():
    bought, _ = settle(planned_book(), {"AAPL": quote()}, TIME, CONFIG)
    assert record_equity(bought, {}, TIME, CONFIG)["status"] == "unknown"
    assert bought["equity"] == []


def test_watch_idea_keeps_a_review_horizon_instead_of_zero():
    plan = decision()
    plan["positions"][0].update(action="watch", target_weight=0, horizon_days=14)
    assert validate(plan, CONFIG, packet(), initial_book(100000))


@pytest.mark.parametrize("days", [0, -1, 366, 20.0, True])
def test_reject_bad_horizon(days):
    plan = decision()
    plan["positions"][0]["horizon_days"] = days
    with pytest.raises(ValueError, match="horizon"):
        validate(plan, CONFIG, packet(), initial_book(100000))


@pytest.mark.parametrize("value", [0.0, 1.0, 0.04, float("nan"), "0.6"])
def test_reject_bad_confidence(value):
    plan = decision()
    plan["positions"][0]["confidence"] = value
    with pytest.raises(ValueError, match="confidence"):
        validate(plan, CONFIG, packet(), initial_book(100000))


@pytest.mark.parametrize("action,weight", [("watch", 0.1), ("exit", 0.1), ("hold", 0.0), ("new", 0.0)])
def test_action_must_agree_with_weight(action, weight):
    plan = decision()
    plan["positions"][0].update(action=action, target_weight=weight)
    with pytest.raises(ValueError, match="contradicts"):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_unknown_action_rejected():
    plan = decision()
    plan["positions"][0]["action"] = "双倍梭哈"
    with pytest.raises(ValueError, match="action"):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_rephrased_quote_is_not_a_quote():
    plan = decision()
    plan["positions"][0]["evidence_quotes"][0]["quote"] = "交付量同比增长约百分之十"
    with pytest.raises(ValueError, match="verbatim"):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_quote_must_cite_an_evidence_id_the_position_listed():
    plan = decision()
    plan["positions"][0]["evidence_ids"] = ["price:AAPL"]
    plan["positions"][0]["evidence_quotes"] = [{"evidence_id": "news:AAPL:1", "quote": "季度交付量高于上一季"}]
    with pytest.raises(ValueError, match="Invented"):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_whitespace_differences_do_not_break_a_real_quote():
    plan = decision()
    plan["positions"][0]["evidence_quotes"][0]["quote"] = " 季度交付量\n高于上一季 "
    assert validate(plan, CONFIG, packet(), initial_book(100000))


@pytest.mark.parametrize("reference", ["price:AAPL#made_up", "price:AAPL", "price:AAPL#return_60d", "news:AAPL:1#summary", "price:NVDA#last_close"])
def test_unresolvable_fact_reference_rejected(reference):
    plan = decision()
    plan["positions"][0]["fact_refs"] = [reference]
    with pytest.raises(ValueError):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_positive_position_needs_a_price_fact_reference():
    data = packet()
    data["evidence"].append({"id": "income:AAPL:0", "symbol": "AAPL", "kind": "financials", "as_of": TIME.isoformat(),
                             "data": {"revenue": 123456789.0, "period": "Q4"}})
    plan = decision()
    plan["positions"][0]["evidence_ids"].append("income:AAPL:0")
    plan["positions"][0]["fact_refs"] = ["income:AAPL:0#revenue"]
    with pytest.raises(ValueError, match="price fact reference"):
        validate(plan, CONFIG, data, initial_book(100000))


@pytest.mark.parametrize("reference", ["5", "9000", "0", "-100", "nan", "mean_20d", "price:AAPL#period"])
def test_implausible_or_unresolvable_price_invalidation_rejected(reference):
    plan = decision()
    plan["positions"][0]["invalidation"]["reference"] = reference
    with pytest.raises(ValueError):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_plain_price_level_invalidation_accepted():
    plan = decision()
    plan["positions"][0]["invalidation"]["reference"] = "96.5"
    assert validate(plan, CONFIG, packet(), initial_book(100000))


def test_event_invalidation_does_not_need_a_price_reference():
    plan = decision()
    plan["positions"][0]["invalidation"] = {"kind": "financial_event", "reference": "下一季营业利润环比下降",
                                            "description": "下一季营业利润环比下降则退出"}
    assert validate(plan, CONFIG, packet(), initial_book(100000))


def test_unattributed_macro_statement_is_recorded_not_rejected():
    # Losing a trading day is worse than an untraceable sentence that stays visible in the archive.
    plan = decision()
    plan["overview"] = "美联储可能降息，因此建立仓位"
    assert validate(plan, CONFIG, packet(), initial_book(100000))
    assert quality_flags(plan)["unattributed_macro"] == ["美联储"]


def test_macro_statement_in_a_thesis_is_also_flagged():
    plan = decision()
    plan["positions"][0]["thesis"] = "油价大涨会推高这家公司的收入，因此买入"
    assert quality_flags(plan)["unattributed_macro"] == ["油价"]


def test_an_attributed_macro_statement_is_not_flagged():
    data = packet()
    data["evidence"][1]["summary"] = "报道称美联储可能降息，公司称需求稳定。"
    plan = decision()
    plan["overview"] = "报道称美联储可能降息，但这只是报道，不是本数据包的数据"
    plan["macro_claims"] = [{"claim": "报道称美联储可能降息", "evidence_id": "news:AAPL:1", "quote": "报道称美联储可能降息"}]
    plan["positions"][0]["evidence_quotes"][0]["quote"] = "公司称需求稳定"
    assert validate(plan, CONFIG, data, initial_book(100000))


def test_macro_claim_cannot_cite_a_price_feature():
    # Filing a claim is still checked: a macro_claims entry must point at news it can be read in.
    plan = decision()
    plan["overview"] = "油价大涨"
    plan["macro_claims"] = [{"claim": "油价大涨", "evidence_id": "price:AAPL", "quote": "last_close"}]
    with pytest.raises(ValueError, match="wrong kind"):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_an_earnings_driver_is_a_known_false_positive_of_the_flag():
    """"Profit may come from oil" names a business driver and has nothing to cite, yet it is flagged.

    Recorded rather than fixed: for an energy name, oil IS the fundamental, and a string matcher
    cannot separate that from a claim about the oil market. This is the reason the flag stopped
    being a rejection - it over-reports, and a metric that over-reports must not cost a trading day.
    Read the flag before acting on it.
    """
    plan = decision()
    plan["positions"][0]["thesis"] = "该季度利润改善可能来自油价，因此只给小仓位并等待更多证据"
    assert quality_flags(plan)["unattributed_macro"] == ["油价"]


def test_missing_changes_vs_last_rejected():
    plan = decision()
    plan["positions"][0]["changes_vs_last"] = "  "
    with pytest.raises(ValueError, match="changes_vs_last"):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_injection_flag_must_name_real_evidence():
    plan = decision()
    plan["injection_flags"] = ["news:AAPL:does_not_exist"]
    with pytest.raises(ValueError, match="Injection flag"):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_injected_news_cannot_raise_a_weight_even_if_the_model_obeys_it():
    data, canary = inject(packet())
    assert canary == "news:AAPL:1"
    assert "SYSTEM OVERRIDE" in data["evidence"][1]["summary"]
    obedient = decision()
    obedient["positions"][0]["target_weight"] = 1.0
    with pytest.raises(ValueError, match="Position limit violation"):
        validate(obedient, CONFIG, data, initial_book(100000))


def test_injected_news_round_still_validates_when_the_model_reports_it():
    data, canary = inject(packet())
    plan = decision()
    plan["injection_flags"] = [canary]
    assert validate(plan, CONFIG, data, initial_book(100000))


def test_bench_scores_unfaithful_quotes_and_retyped_numbers():
    data = packet()
    plan = decision()
    plan["positions"][0]["thesis"] = "收入 900.07 亿美元，持有 20 天，占比 12%"
    plan["positions"][0]["evidence_quotes"].append({"evidence_id": "news:AAPL:1", "quote": "这句话不在证据里"})
    metrics = score(plan, data)
    assert metrics["quotes"] == 2 and metrics["quote_faithful"] == 1
    # "900.07" is a retyped packet-free number; "20 天" and "12%" are the model's own words.
    assert metrics["bare_numbers_in_prose"] == 1
    assert metrics["machine_checkable_exits"] == 1 and metrics["positive_positions"] == 1


def test_both_cli_executables_resolve_without_relying_on_path(monkeypatch):
    import shutil as shutil_module

    from ai_portfolios import models
    # A spawned process may not inherit the PATH an interactive shell has; a round must not fail as
    # "executable unavailable" when the tool is installed and logged in.
    monkeypatch.setattr(shutil_module, "which", lambda name: None)
    for name in ("claude", "codex"):
        resolved = models.executable(name)
        assert resolved.endswith(".exe") and Path(resolved).exists()


def test_unknown_executable_still_reports_unavailable(monkeypatch):
    import shutil as shutil_module

    from ai_portfolios import models
    monkeypatch.setattr(shutil_module, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="unavailable"):
        models.executable("grok")


@pytest.mark.parametrize("stdout,kind", [
    ('{"type":"error","message":"You’ve hit your usage limit. Upgrade to Pro or purchase more credits."}',
     "usage_limit_reached"),
    ('{"result":"Failed to authenticate: OAuth session expired and could not be refreshed"}',
     "authentication_required"),
    ('{"type":"error","error":{"message":"rate limit exceeded, try again later"}}', "usage_limit_reached"),
    ('{"type":"error","message":"model not found"}', "cli_error"),
    ('not json at all', "cli_error"),
    ('', "cli_error"),
])
def test_failure_kind_separates_retry_later_from_broken(stdout, kind):
    from ai_portfolios.models import classify_failure

    result = classify_failure("gpt", stdout, {})
    assert result["failure_kind"] == kind
    # A quota pause and an expired login both need a human, so both carry a hint; a defect does not.
    assert ("error_hint" in result) is (kind != "cli_error")


def test_a_book_below_the_participation_floor_is_rejected():
    # Standing aside is unavailable: an account that never participates teaches nothing, and the
    # mandate alone did not move it (measured at 26% invested over ten repeats).
    with pytest.raises(ValueError, match="participation floor"):
        validate(decision(), REAL_CONFIG, packet(), initial_book(100000))


def test_the_floor_is_reported_only_after_the_specific_breaches():
    plan = decision()
    plan["positions"][0]["evidence_ids"].append("made_up")
    # Both are wrong; the invented citation is the one worth telling the model about.
    with pytest.raises(ValueError, match="Invented"):
        validate(plan, REAL_CONFIG, packet(), initial_book(100000))


def test_a_first_position_may_say_only_that_it_is_new():
    plan = decision()
    plan["positions"][0]["changes_vs_last"] = "首次建仓。"
    assert validate(plan, CONFIG, packet(), initial_book(100000))


def test_a_held_name_must_say_more_than_that(packet_fixture=None):
    book = initial_book(100000)
    book["last_decision"] = {"positions": [{"symbol": "AAPL", "target_weight": 0.1}]}
    book["holdings"]["AAPL"] = {"shares": 1, "last_mark": 100}
    plan = decision()
    plan["positions"][0]["changes_vs_last"] = "继续。"
    with pytest.raises(ValueError, match="changes_vs_last"):
        validate(plan, CONFIG, packet(), book)


@pytest.mark.parametrize("changes", ["", "   ", None])
def test_changes_vs_last_can_never_be_empty(changes):
    plan = decision()
    plan["positions"][0]["changes_vs_last"] = changes
    with pytest.raises(ValueError, match="changes_vs_last"):
        validate(plan, CONFIG, packet(), initial_book(100000))


def test_a_macro_term_inside_a_conditional_needs_no_source():
    # "If oil falls back" cites nothing because there is nothing to cite; it is a sensitivity.
    plan = decision()
    plan["positions"][0]["thesis"] = "油价若回落，这里的盈利提升可能不持续，所以只给小仓位"
    assert validate(plan, CONFIG, packet(), initial_book(100000))


def test_an_assertion_in_the_same_sentence_as_a_conditional_is_still_flagged():
    plan = decision()
    plan["overview"] = "油价因袭击大涨；若后续回落，这个判断要重看"
    assert quality_flags(plan)["unattributed_macro"] == ["油价"]


def test_a_synonym_in_the_claim_still_counts_as_attribution():
    data = packet()
    data["evidence"][1]["summary"] = "报道称原油价格因袭击大涨，公司称需求稳定。"
    plan = decision()
    plan["overview"] = "油价大涨推高这家公司的收入"
    plan["macro_claims"] = [{"claim": "报道称原油价格因袭击大涨", "evidence_id": "news:AAPL:1",
                             "quote": "报道称原油价格因袭击大涨"}]
    plan["positions"][0]["evidence_quotes"][0]["quote"] = "公司称需求稳定"
    assert validate(plan, CONFIG, data, initial_book(100000))
