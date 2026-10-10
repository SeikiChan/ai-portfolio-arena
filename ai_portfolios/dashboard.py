from __future__ import annotations

import html
import json
from pathlib import Path

from .common import DATA, stamp


ACTION_LABELS = {"new": "新建", "add": "加仓", "hold": "继续持有", "trim": "减仓", "exit": "退出", "watch": "观察"}


def esc(value):
    return html.escape(str(value))


def resolved(reference, facts):
    # The model names the field; the report prints the value, so no number is retyped twice.
    identity, _, field = reference.partition("#")
    value = facts.get(identity, {}).get(field)
    return f"{esc(reference)} = {esc(f'{value:,.4g}' if isinstance(value, float) else value)}" if value is not None else f"{esc(reference)} = 数据缺失"


def idea_html(position, facts):
    weight = position["target_weight"]
    action = ACTION_LABELS.get(position.get("action"), "目标配置" if weight > 0 else "退出 / 观察")
    horizon = position.get("horizon_days", position.get("expected_holding_days", "—"))
    rule = position.get("invalidation") if isinstance(position.get("invalidation"), dict) else None
    failure = f'{rule["description"]}（{rule["kind"]} · {rule["reference"]}）' if rule else position.get("exit_condition", "本轮未提供")
    confidence = f' · 信心 {position["confidence"]:.0%}' if isinstance(position.get("confidence"), float) else ""
    changes = f'<p class="muted"><b>相对上轮</b> {esc(position["changes_vs_last"])}</p>' if position.get("changes_vs_last") else ""
    numbers = " · ".join(resolved(r, facts) for r in position.get("fact_refs", []))
    quotes = "".join(f'<li>{esc(q["evidence_id"])}：{esc(q["quote"])}</li>' for q in position.get("evidence_quotes", []))
    return (f'<details class="idea"><summary><b>{esc(position["symbol"])}</b><span>{esc(action)} {weight:.0%}</span>'
            f'<small>预期 {esc(horizon)} 天 · 可提前复核{esc(confidence)}</small></summary><p>{esc(position["thesis"])}</p>{changes}'
            f'<p class="exit"><b>失效条件</b> {esc(failure)}</p>'
            + (f'<p class="muted"><b>引用数值</b> {numbers}</p>' if numbers else "")
            + (f'<ul class="muted">{quotes}</ul>' if quotes else "")
            + f'<small>证据：{esc(", ".join(position["evidence_ids"]))}</small></details>')


def render(state, packet=None):
    packet = packet or state.get("last_packet_summary", {})
    cards = []
    for name in ("claude", "gpt"):
        book = state["books"][name]
        run = state.get("model_results", {}).get(name, {})
        decision = run.get("decision") or book.get("last_decision") or {}
        accepted = run.get("status") == "validated"
        status = {"validated": "决策已通过检查", "failed": "本轮调用失败", "running": "正在独立研究"}.get(run.get("status"), "等待首次研究")
        fills = sum(len(e.get("fills", [])) for e in book["events"])
        marked = state.get("marks", {}).get(name, {})
        value = marked.get("equity")
        equity = f"${value:,.2f}" if value is not None else "估值待更新"
        returns = f"{(value / book['initial_cash'] - 1) * 100:+.2f}%" if fills and value is not None else "尚未形成"
        history = book["equity"]
        peak, dd = book["initial_cash"], 0.0
        for row in history:
            peak = max(peak, row["equity"])
            dd = min(dd, row["equity"] / peak - 1)
        facts = {row["id"]: row.get("data") or {} for row in packet.get("evidence", [])}
        rows = [idea_html(p, facts) for p in decision.get("positions", [])]
        pending = "等待决策完成后新的有效报价，尚未成交" if book.get("pending") else "没有待执行计划"
        error = f'<div class="warning">{esc(run.get("error", ""))}</div>' if run.get("status") == "failed" else ""
        if run.get("status") == "failed" and book.get("last_decision"):
            error += '<p class="muted">下方保留上轮决定，并非本轮新决定。</p>'
        gaps = "；".join(decision.get("evidence_gaps", []))
        actual = "、".join(f'{s} {p["shares"]:.4f} 股' for s, p in book["holdings"].items()) or "现金 · 尚无模拟持仓"
        cards.append(f'''<section class="card {name}"><div class="cardtop"><span class="avatar">{'C' if name == 'claude' else 'G'}</span><div><h2>{name.upper()}</h2><span class="muted">{esc(run.get('model_requested', state['config'][name + '_model']))}</span></div><span class="pill {'good' if accepted else ''}">{status}</span></div>
        {error}<div class="metrics"><div><label>模拟净值</label><strong>{equity}</strong></div><div><label>价格收益率</label><strong>{returns}</strong></div><div><label>已观察最大回撤</label><strong>{f'{dd:.2%}' if fills else '尚未形成'}</strong></div></div>
        <p class="thesis">{esc(decision.get('overview', '正在读取同一份冻结数据，独立形成投资决定。'))}</p>
        <div class="subhead">建议组合 <span>建议不等于已成交</span></div>{''.join(rows) or '<p class="muted">暂无有效目标配置；不会用另一模型的答案替代。</p>'}
        <p><b>现金理由</b> {esc(decision.get('cash_reason', '等待研究结果'))}</p>
        <details><summary>持仓与数据缺口</summary><p>当前模拟持仓：{esc(actual)}</p><p>模拟现金：${book['cash']:,.2f} · 模拟成交 {fills} 笔</p><p>{esc(gaps or '暂无模型缺口说明')}</p><p>执行状态：{esc(state.get('settlement', {}).get(name, pending))}</p></details>
        <div class="foot">{esc(pending)}<br>决策完成：{esc(run.get('completed_at', '—'))}</div></section>''')
    evidences = packet.get("evidence", [])
    evidence_rows = []
    for row in evidences:
        text = row.get("headline") or json.dumps(row.get("data", {}), ensure_ascii=False)
        url = row.get("url", "") or ""
        link = f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">原文 ↗</a>' if url.startswith("https://") else ""
        evidence_rows.append(f'<tr><td>{esc(row["id"])}</td><td>{esc(row.get("as_of"))}</td><td>{esc(text[:700])} {link}</td></tr>')
    errors = packet.get("errors", [])
    news_count = sum(e["kind"] == "news" for e in evidences)
    financial_count = sum(e["kind"] == "financials" for e in evidences)
    count_bars = sum(e.get("daily_rows", 0) for e in packet.get("features", []))
    config = state["config"]
    benchmark_cards = []
    for symbol in config["benchmarks"]:
        book = state["books"][symbol]
        point = state.get("marks", {}).get(symbol, {})
        filled = any(e.get("fills") for e in book["events"])
        val = point.get("return")
        benchmark_cards.append(f'<div><b>{symbol}</b><span>{f"{val:+.2%}" if filled and val is not None else "等待首次模拟成交"}</span><small>同批启动 · 买入持有 · 95% 配置</small></div>')
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="90"><title>双模型投资观察台</title><style>
    :root{{color-scheme:dark;--bg:#0c1420;--card:#152132;--muted:#94a6bc;--line:#2b3b50;--green:#7bddb7}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:#e7eef7;font:15px/1.7 'Segoe UI','Microsoft YaHei',sans-serif}}main{{max-width:1360px;margin:auto;padding:38px 28px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:start}}h1{{font-size:32px;margin:5px 0}}h2{{margin:0;font-size:23px}}.eyebrow{{letter-spacing:3px;font-size:12px;color:var(--green)}}.muted,small,label{{color:var(--muted)}}.tag,.pill{{padding:5px 11px;border:1px solid var(--line);border-radius:20px;font-size:12px;white-space:nowrap}}.tag{{color:#f3cc7d}}.good{{color:var(--green);background:#15382f}}.notice{{border-left:3px solid #e7bb65;background:#202734;padding:14px 18px;margin:25px 0}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:22px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:25px}}.cardtop{{display:flex;gap:13px;align-items:center}}.cardtop .pill{{margin-left:auto}}.avatar{{display:grid;place-items:center;width:45px;height:45px;border-radius:12px;background:#5c403a;color:#f6c8af;font-weight:700;font-size:24px}}.gpt .avatar{{background:#214e46;color:#a1ead4}}.metrics{{display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:12px;margin:28px 0}}label{{display:block;font-size:12px}}strong{{display:block;font-size:22px}}.thesis{{min-height:78px}}.subhead{{font-weight:bold;border-top:1px solid var(--line);padding-top:18px}}.subhead span{{float:right;font-size:12px;font-weight:normal;color:var(--muted)}}details{{margin:12px 0}}summary{{cursor:pointer}}.idea{{background:#101b2a;border-radius:9px;padding:12px 14px}}.idea summary{{display:flex;gap:14px;flex-wrap:wrap}}.idea summary small{{margin-left:auto}}.idea p{{margin:12px 0 4px}}.exit{{color:#c5d6e7}}.foot{{border-top:1px solid var(--line);padding-top:14px;font-size:12px;color:var(--muted)}}.warning{{color:#ffc17c;overflow-wrap:anywhere}}.bench{{display:flex;gap:18px;margin:22px 0}}.bench>div{{flex:1;border:1px solid var(--line);border-radius:12px;padding:16px}}.bench span{{float:right;color:var(--green)}}.bench small{{display:block}}.audit{{margin-top:24px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px;overflow-wrap:anywhere}}table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{text-align:left;border-bottom:1px solid var(--line);padding:12px;vertical-align:top}}a{{color:#9fdcfa}}.tablewrap{{overflow:auto;max-height:600px}}footer{{font-size:12px;color:var(--muted);margin-top:24px}}@media(max-width:950px){{.grid{{grid-template-columns:1fr}}header{{display:block}}.tag{{display:inline-block;margin-top:12px}}}}@media(max-width:550px){{main{{padding:18px 12px}}.card{{padding:18px}}.cardtop{{flex-wrap:wrap}}.cardtop .pill{{margin-left:0}}strong{{font-size:18px}}.bench{{flex-direction:column}}}}
    </style></head><body><main><header><div><div class="eyebrow">INDEPENDENT AI PORTFOLIOS / 01</div><h1>双模型投资观察台</h1><div class="muted">相同证据 · 独立判断 · 连续持仓复核</div></div><span class="tag">前向模拟研究 · 不连接实盘交易</span></header>
    <div class="notice">当前是新启动的观察组合。下方配置为 AI 建议，只有决策完成后取得新的有效报价才记模拟成交。尚无足够时间证明收益优势；不存在补造的历史业绩。页面每 90 秒重读已生成结果，不会自行触发模型调用。</div>
    <div class="grid">{''.join(cards)}</div><div class="bench">{''.join(benchmark_cards)}</div>
    <section class="audit"><h2>共同规则与证据</h2><p>每组虚拟初始资金 ${config['initial_cash']:,.0f}；股票池 {len(config['universe'])} 只；最多 {config['max_positions']} 只正仓位；单股目标 ≤ {config['max_weight']:.0%}；总目标仓位 ≤ {config['max_invested']:.0%}。不强制周末调仓，持仓时间由模型复核决定。这些是本实验设定，并非 Rallies 原版。</p>
    <p>本轮证据：日线 {count_bars:,} 行 · 新闻摘要 {news_count} 条 · 财务记录 {financial_count} 条 · 数据请求缺口 {len(errors)} 项。IEX 为单一交易所行情，非全市场成交量 / NBBO。</p>
    <p>研究快照：{esc(packet.get('snapshot_id','采集中'))}<br>数据冻结：{esc(packet.get('frozen_at','—'))}<br>AM70/30 事实账本引用：{esc(packet.get('truth_reference',{}).get('snapshot_id','—'))}</p>
    <details><summary>查看原始证据索引</summary><div class="tablewrap"><table><thead><tr><th>证据 ID</th><th>时间</th><th>内容</th></tr></thead><tbody>{''.join(evidence_rows)}</tbody></table></div></details>
    <details><summary>查看数据缺口与计算边界</summary><p>{esc(json.dumps(errors,ensure_ascii=False))}</p><p>实际费用、真实滑点、市场冲击：unknown。模拟以之后取得的 bid/ask 假定可成交，不保证真实可成交数量。另记每边 {config['stress_cost_bps_per_side']} bp 压力成本，属于 assumption，不冒充真实成本。价格收益不含股息；疑似大幅拆股/价格断点暂停估值与成交并等待核对。</p></details></section>
    <footer>生成于 {esc(stamp())} · 模型理由属于研究判断，不是事实认证。可执行源代码和每轮输入、输出、配置、哈希均保存在本机。通过“运行研究.ps1”产生新一轮结果。</footer></main></body></html>'''
    target = DATA / "dashboard.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(page, encoding="utf-8")
    temporary.replace(target)
    return target
