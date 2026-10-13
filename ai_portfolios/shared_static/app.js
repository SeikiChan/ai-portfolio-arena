"use strict";
const $ = id => document.getElementById(id);
const el = (tag, value, cls) => { const n=document.createElement(tag); if(value!=null)n.textContent=String(value); if(cls)n.className=cls; return n; };
const money = value => value==null||!Number.isFinite(Number(value)) ? "未知" : new Intl.NumberFormat("zh-CN",{style:"currency",currency:"USD",maximumFractionDigits:2}).format(Number(value));
const pct = value => value==null||!Number.isFinite(Number(value)) ? "未知" : (Number(value)*100).toFixed(1)+"%";
const time = value => value&&!Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN",{hour12:false}) : (value||"—");
const picked={}, tabs={};
let renderedSignature=null;
function link(url) {
  if(!/^https:\/\//i.test(url||"")) return null;
  const a=el("a","原文 ↗"); a.href=url; a.target="_blank"; a.rel="noopener noreferrer"; return a;
}
function chart(points) {
  const box=el("section",null,"chart");
  box.append(el("div","PAPER 净值 / 已观察快照","eyebrow"));
  const rows=(points||[]).filter(p=>p&&p.at&&Number.isFinite(Number(p.equity)));
  if(!rows.length){box.append(el("p","暂无可核对的账户净值快照。","muted"));return box;}
  const ns="http://www.w3.org/2000/svg";
  const s=(tag,attrs={})=>{const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,String(v)));return n;};
  const svg=s("svg",{viewBox:"0 0 720 210",role:"img","aria-label":"Paper 账户净值快照，实验版本之间断线"});
  const values=rows.map(p=>Number(p.equity)), lo=Math.min(...values),hi=Math.max(...values),pad=Math.max((hi-lo)*0.2,hi*0.002,1);
  const y=v=>170-(Number(v)-(lo-pad))/(hi-lo+2*pad)*135;
  const x=i=>rows.length===1?360:65+i*610/(rows.length-1);
  for(let i=0;i<3;i++){const yy=35+i*67.5;svg.append(s("line",{x1:65,y1:yy,x2:680,y2:yy,class:"gridline"}));const label=s("text",{x:3,y:yy+4,class:"axis"});label.textContent=money(hi+pad-i*(hi-lo+2*pad)/2);svg.append(label);}
  let segment=[];
  const flush=()=>{if(segment.length>1)svg.append(s("polyline",{points:segment.map(i=>x(i)+","+y(rows[i].equity)).join(" "),class:"trend"}));segment=[];};
  rows.forEach((p,i)=>{if(segment.length&&rows[segment[0]].experiment_id!==p.experiment_id)flush();segment.push(i);});
  flush();
  rows.forEach((p,i)=>{const dot=s("circle",{cx:x(i),cy:y(p.equity),r:5,class:"dot"}),title=s("title");title.textContent=time(p.at)+" · "+money(p.equity)+" · "+(p.experiment_id||"版本未知");dot.append(title);svg.append(dot);});
  box.append(svg,el("p",rows.length+" 个真实快照点。实验版本变更处断线；仅为采样时点，不是完整盘中走势。","muted"));
  return box;
}
function decision(round) {
  const host=el("div",null,"tab-body");
  if(round.error)host.append(el("p","研究失败："+round.error,"alert"));
  if(round.execution_error)host.append(el("p","执行中止："+round.execution_error,"alert"));
  if(round.overview)host.append(el("p",round.overview,"overview"));
  const source=new Map((round.decision_evidence||[]).map(e=>[e.id,e]));
  if(!round.decision_details?.length)host.append(el("p","本轮没有通过校验的组合决定。","muted"));
  for(const p of round.decision_details||[]){
    const card=el("article",null,"idea"),head=el("div",null,"idea-head");
    head.append(el("h4",p.symbol),el("span",(p.action||"目标")+" · "+pct(p.target_weight),"pill"));card.append(head);
    card.append(el("p",p.thesis||"没有论点文本。"));
    if(p.changes_vs_last)card.append(el("p","相对上轮："+p.changes_vs_last,"muted"));
    if(p.invalidation)card.append(el("p","复核条件："+(p.invalidation.description||"未说明"),"muted"));
    card.append(el("p","复核期限 "+(p.horizon_days??"—")+" 天 · 信心 "+pct(p.confidence),"muted"));
    if(p.facts?.length){const facts=el("details");facts.append(el("summary","引用的数值"));p.facts.forEach(f=>facts.append(el("p",f.ref+" = "+(f.value??"缺失"),"fact")));card.append(facts);}
    if(p.evidence_ids?.length){
      const evidence=el("details");evidence.append(el("summary","查看证据与原文 · "+p.evidence_ids.length+" 项"));
      for(const id of p.evidence_ids){const row=source.get(id),item=el("div",null,"evidence");
        item.append(el("b",id),el("span"," · "+(row?.kind||"未发布")+" · "+time(row?.as_of)));
        if(row?.headline)item.append(el("p",row.headline));
        const a=link(row?.url);if(a)item.append(a);
        const q=(p.evidence_quotes||[]).find(v=>v.evidence_id===id);if(q)item.append(el("blockquote",q.quote));
        evidence.append(item);
      }card.append(evidence);
    }host.append(card);
  }
  if(round.cash_reason)host.append(el("h4","现金与仓位"),el("p",round.cash_reason));
  if(round.evidence_gaps?.length){host.append(el("h4","数据缺口"));round.evidence_gaps.forEach(g=>host.append(el("p",g,"muted")));}
  return host;
}
function research(round) {
  const host=el("div",null,"tab-body");
  host.append(el("p","以下是已归档的模型研究动作与公开工具结果。内部推理不可见，最终原始回复可在下方展开。","muted"));
  if(round.research_enabled&&round.research_trace?.length)host.append(el("p","历史指令里的“没有工具”仅指最后生成组合的那次调用；本轮前置研究实际使用了下面这些联网工具。旧轮次原文保持不变。","muted"));
  if(!round.research_trace?.length)host.append(el("p","本轮没有保存公开研究工具对话。","muted"));
  for(const step of round.research_trace||[]){
    const card=el("article",null,"trace "+step.kind);
    card.append(el("b",step.kind==="model"?"模型研究步骤 "+step.step+(step.done?" · 结束":" · 请求资料"):"工具调用 "+step.step+" · "+step.status));
    if(step.kind==="model"){(step.actions||[]).forEach(a=>card.append(el("p",(a.tool||"工具")+"："+(a.query||a.url||a.expression||"—"))));}
    else{const a=step.action||{};card.append(el("p",(a.tool||"工具")+"："+(a.query||a.url||a.expression||"—")));
      const source=link(step.url);if(source)card.append(source);
      if(step.text)card.append(el("pre",step.text+(step.excerpt_truncated?"\n[页面摘录已截断，原始资料仍在轮次档案中]":"")));
      card.append(el("small",(step.classification||"unknown")+" · 来源哈希 "+(step.source_sha256||"未提供")));
    }host.append(card);
  }
  for(const [label,value] of [["最终原始回复",round.response||round.error||"没有模型回复"],["最终决策阶段的指令（前置研究可用工具；账户输入已省略）",round.full_prompt||"没有指令存档"]]){
    const details=el("details",null,"transcript");details.append(el("summary",label),el("pre",value));host.append(details);
  }
  return host;
}
function execution(round) {
  const host=el("div",null,"tab-body"),a=round.account_observed||{},mark=round.equity_mark,metrics=el("div",null,"metrics");
  for(const [label,value] of [["Paper 净值",a.equity??mark?.equity],["现金",a.cash],["购买力",a.buying_power]]){const card=el("div",null,"metric");card.append(el("label",label),el("strong",money(value)));metrics.append(card);}
  host.append(metrics,el("p","净值来源："+(a.equity!=null?"执行前账户读取":mark?"研究前账户快照":"缺失")+" · 执行状态："+(round.execution_mode||"未知"),"muted"));
  if(round.execution_error)host.append(el("p",round.execution_error,"alert"));
  host.append(el("h4","本轮委托"));
  if(!round.broker_orders?.length)host.append(el("p","本轮没有提交新委托；目标权重不是成交。","muted"));
  for(const o of round.broker_orders||[]){const q=o.request||{};host.append(el("p",(q.side||"—")+" "+(q.qty||"—")+" 股 "+(q.symbol||"—")+" · "+(o.outcome||"未知")+" · "+(o.broker_status||o.broker_message||o.error||""),"order"));}
  if(a.positions?.length){const table=el("table"),head=el("tr");["标的","股数","市值","未实现盈亏"].forEach(v=>head.append(el("th",v)));table.append(head);
    a.positions.forEach(p=>{const tr=el("tr");[p.symbol,p.qty,money(p.market_value),money(p.unrealized_pl)].forEach(v=>tr.append(el("td",v)));table.append(tr);});host.append(el("h4","执行前持仓"),table);
  }return host;
}
function teamView(team) {
  const name=team.engine_id==="codex_engine"?"Codex":"Claude",box=el("article",null,"team "+name.toLowerCase()),head=el("div",null,"team-head");
  head.append(el("h2",name+" 团队"),el("span",team.status==="available"?"档案可读":"等待发布","pill"));box.append(head,el("p","最近发布 "+time(team.published_at)+" · "+(team.rounds?.length||0)+" 轮档案","muted"),chart(team.equity_history));
  (team.rejected_rounds||[]).forEach(r=>box.append(el("p","未发布轮次 "+r.round_id+"："+r.reason,"alert")));
  if(!team.rounds?.length){box.append(el("p",team.note||"暂无轮次","muted"));return box;}
  const selector=el("select");selector.setAttribute("aria-label","选择 "+name+" 轮次");
  team.rounds.forEach(r=>{const opt=el("option",time(r.completed_at)+" · "+r.status+" · "+(r.orders_submitted?"已提交":"无新委托"));opt.value=r.id;selector.append(opt);});
  if(!team.rounds.some(r=>r.id===picked[team.team_id]))picked[team.team_id]=team.rounds[0].id;
  selector.value=picked[team.team_id];box.append(el("label","选择研究轮次","select-label"),selector);
  const body=el("div");box.append(body);
  function show(){const r=team.rounds.find(x=>x.id===picked[team.team_id])||team.rounds[0];body.replaceChildren();
    body.append(el("p","轮次 "+r.round_id+" · Packet "+(r.packet_sha256||"未知").slice(0,12)+" · 实验 "+(r.experiment_id||"未记录"),"round-meta"));
    const nav=el("nav",null,"tabs"),current=tabs[team.team_id]||"decision";
    for(const [id,label] of [["decision","决策依据"],["research","研究对话"],["execution","持仓与执行"]]){const button=el("button",label,id===current?"active":"");button.type="button";button.onclick=()=>{tabs[team.team_id]=id;show();};nav.append(button);}
    body.append(nav,({decision,research,execution})[current](r));
  }
  selector.onchange=()=>{picked[team.team_id]=selector.value;show();};show();return box;
}
async function refresh(){
  try{const response=await fetch("/api/teams",{cache:"no-store"}),data=await response.json();if(!response.ok)throw Error(data.error||"发布暂不可读");
    const signature=JSON.stringify((data.teams||[]).map(team=>[team.team_id,team.published_at,team.status]));
    $("message").textContent="";
    if(signature!==renderedSignature){$("teams").replaceChildren(...(data.teams||[]).map(teamView));renderedSignature=signature;}
    $("updated").textContent="页面更新 "+new Date().toLocaleTimeString("zh-CN",{hour12:false});
  }catch(error){$("message").textContent="读取失败："+error.message;}
}
refresh();setInterval(refresh,30000);
