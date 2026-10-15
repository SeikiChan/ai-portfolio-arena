"use strict";
const $ = id => document.getElementById(id);
const el = (tag, value, cls) => { const n=document.createElement(tag); if(value!=null)n.textContent=String(value); if(cls)n.className=cls; return n; };
const money = value => value==null||!Number.isFinite(Number(value)) ? "未知" : new Intl.NumberFormat("zh-CN",{style:"currency",currency:"USD",maximumFractionDigits:2}).format(Number(value));
const pct = value => value==null||!Number.isFinite(Number(value)) ? "未知" : (Number(value)*100).toFixed(1)+"%";
const time = value => value&&!Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN",{hour12:false}) : (value||"—");
const picked={}, tabs={};
const feedState={team:"all",action:"all",limit:8};
let renderedSignature=null;
let currentTeams=[];
const teamName=team=>team.engine_id==="codex_engine"?"Codex":"Claude";
const actionGroup=action=>({new:"buy",add:"buy",trim:"sell",exit:"sell",hold:"hold",watch:"hold"})[action]||"other";
const actionLabel=action=>({new:"新建",add:"加仓",hold:"持有",trim:"减持",exit:"退出",watch:"观察"})[action]||"组合";
const actionGroupLabel=group=>({buy:"买入 / 加仓",sell:"减持 / 退出",hold:"持有 / 观察"})[group]||"全部动态";
const statusLabel=status=>({validated:"通过校验",completed:"已完成",failed:"失败",unknown:"状态未知"})[status]||status||"状态未知";
function link(url) {
  if(!/^https:\/\//i.test(url||"")) return null;
  const a=el("a","原文 ↗"); a.href=url; a.target="_blank"; a.rel="noopener noreferrer"; return a;
}
function feedTrace(step) {
  const card=el("article",null,"feed-trace "+(step.kind||""));
  card.append(el("b",step.kind==="model"?"模型研究步骤 "+step.step+(step.done?" · 完成":" · 请求资料"):"工具调用 "+step.step+" · "+(step.status||"状态未知")));
  if(step.kind==="model"){
    (step.actions||[]).forEach(a=>card.append(el("p",(a.tool||"工具")+"："+(a.query||a.url||a.expression||"—"))));
  }else{
    const a=step.action||{};card.append(el("p",(a.tool||"工具")+"："+(a.query||a.url||a.expression||"—")));
    const source=link(step.url);if(source)card.append(source);
    if(step.text)card.append(el("pre",step.text+(step.excerpt_truncated?"\n[页面摘录已截断]":"")));
    card.append(el("small",(step.classification||"unknown")+" · 来源哈希 "+(step.source_sha256||"未提供")));
  }
  return card;
}
function feedDecision(position) {
  const group=actionGroup(position.action),card=el("article",null,"feed-decision "+group),head=el("div",null,"feed-decision-head");
  head.append(el("strong",position.symbol||"未命名标的"),el("span",actionLabel(position.action)+" · 目标 "+pct(position.target_weight),"decision-tag"));
  card.append(head);
  if(position.thesis)card.append(el("p",position.thesis,"feed-thesis"));
  if(position.changes_vs_last)card.append(el("p","相对上轮："+position.changes_vs_last,"feed-change"));
  if(position.evidence_ids?.length)card.append(el("p","引用证据："+position.evidence_ids.join(" · "),"feed-evidence"));
  card.append(el("p","复核期限 "+(position.horizon_days??"—")+" 天 · 信心 "+pct(position.confidence),"feed-detail"));
  return card;
}
function feedCard(item,isLatest) {
  const {team,round}=item,name=teamName(team),trace=round.research_trace||[],positions=round.decision_details||[];
  const card=el("article",null,"feed-card "+name.toLowerCase()),head=el("div",null,"feed-card-head"),identity=el("div",null,"feed-identity");
  identity.append(el("span",name==="Codex"?"G":"C","team-avatar"),el("strong",name),el("span",statusLabel(round.status),"status-tag "+(round.status||"unknown")));
  if(isLatest)identity.append(el("span","最新一轮","latest-tag"));
  head.append(identity,el("time",time(round.completed_at),"feed-time"));card.append(head);
  card.append(el("p","轮次 "+(round.round_id||round.id||"未知")+" · 实验 "+(round.experiment_id||"未记录"),"feed-round"));
  if(round.error)card.append(el("p","研究失败："+round.error,"alert"));
  if(round.execution_error)card.append(el("p","执行中止："+round.execution_error,"alert"));
  if(round.overview)card.append(el("p",round.overview,"feed-overview"));
  else if(!round.error)card.append(el("p","本轮没有可展示的决策摘要。","muted"));
  if(positions.length){
    const decisions=el("div",null,"feed-decisions");
    positions.forEach(position=>decisions.append(feedDecision(position)));
    card.append(decisions);
  }else if(!round.error){card.append(el("p","本轮没有通过校验的组合决定。","muted"));}
  if(round.cash_reason)card.append(el("p","现金安排："+round.cash_reason,"feed-cash"));
  const audit=el("details",null,"feed-audit"),summary=el("summary","查看完整回复与研究记录 · "+trace.length+" 个研究步骤");
  audit.append(summary);
  audit.append(el("h4","模型最终回复"),el("pre",round.response||round.error||"没有保存模型回复。"));
  audit.append(el("h4","公开研究过程"));
  if(trace.length)trace.forEach(step=>audit.append(feedTrace(step)));
  else audit.append(el("p","本轮没有保存公开研究工具对话。","muted"));
  card.append(audit);
  if(round.broker_orders?.length){
    const orders=el("details",null,"feed-orders");orders.append(el("summary","查看实际委托状态 · "+round.broker_orders.length+" 项"));
    round.broker_orders.forEach(order=>{const q=order.request||{};orders.append(el("p",(q.side||"—")+" "+(q.qty||"—")+" 股 "+(q.symbol||"—")+" · "+(order.outcome||"未知")+" · "+(order.broker_status||order.broker_message||order.error||""),"order"));});
    card.append(orders);
  }
  return card;
}
function filterButton(label,selected,onClick) {
  const button=el("button",label,"filter-chip"+(selected?" active":""));button.type="button";button.setAttribute("aria-pressed",selected?"true":"false");button.onclick=onClick;return button;
}
function renderFeed(teams) {
  const events=(teams||[]).flatMap(team=>(team.rounds||[]).map(round=>({team,round})))
    .sort((a,b)=>(Date.parse(b.round.completed_at)||0)-(Date.parse(a.round.completed_at)||0));
  const latestByTeam=new Map();events.forEach(({team,round})=>{if(!latestByTeam.has(team.team_id))latestByTeam.set(team.team_id,round.id);});
  const teamFilters=$("team-filters"),actionFilters=$("action-filters");teamFilters.replaceChildren();actionFilters.replaceChildren();
  teamFilters.append(filterButton("全部团队",feedState.team==="all",()=>{feedState.team="all";feedState.limit=8;renderFeed(currentTeams);}));
  teams.forEach(team=>teamFilters.append(filterButton(teamName(team),feedState.team===team.team_id,()=>{feedState.team=team.team_id;feedState.limit=8;renderFeed(currentTeams);})));
  actionFilters.append(filterButton("全部动态",feedState.action==="all",()=>{feedState.action="all";feedState.limit=8;renderFeed(currentTeams);}));
  [["buy","买入 / 加仓"],["sell","减持 / 退出"],["hold","持有 / 观察"]].forEach(([id,label])=>actionFilters.append(filterButton(label,feedState.action===id,()=>{feedState.action=id;feedState.limit=8;renderFeed(currentTeams);})));
  const selected=events.filter(({team,round})=>(feedState.team==="all"||feedState.team===team.team_id)&&(feedState.action==="all"||(round.decision_details||[]).some(p=>actionGroup(p.action)===feedState.action)));
  const visible=selected.slice(0,feedState.limit),feed=$("feed");feed.replaceChildren();
  if(!selected.length)feed.append(el("p","没有符合筛选条件的已发布轮次。","empty-feed"));
  else visible.forEach(item=>feed.append(feedCard(item,latestByTeam.get(item.team.team_id)===item.round.id)));
  $("feed-count").textContent=selected.length?"显示 "+visible.length+" / "+selected.length+" 轮":"0 轮";
  if(visible.length<selected.length){const more=el("button","加载更早轮次","load-more");more.type="button";more.onclick=()=>{feedState.limit+=8;renderFeed(currentTeams);};feed.append(more);}
}
function chartView(teams) {
  const box=el("section",null,"comparison-panel");
  const historyRows=points=>(points||[]).filter(p=>p&&Number.isFinite(Date.parse(p.at))&&Number.isFinite(Number(p.equity)))
    .map(p=>({...p,atMs:Date.parse(p.at),equity:Number(p.equity)})).sort((a,b)=>a.atMs-b.atMs);
  const experimentId=p=>typeof p.experiment_id==="string"&&p.experiment_id.trim()?p.experiment_id.trim():null;
  const series=(teams||[]).map(team=>{
    const history=team.portfolio_history,hasHistory=history?.status==="available"&&(history.points||[]).length>1;
    return {team,name:teamName(team),key:teamName(team).toLowerCase(),hasHistory,
      points:historyRows(hasHistory?history.points:team.equity_history),
      snapshots:historyRows(team.equity_history)};
  });
  const available=series.filter(item=>item.points.length);
  const summary=el("div",null,"comparison-summary");
  const commonStart=available.length?Math.max(...available.map(item=>item.points[0].atMs)):null;
  const commonEnd=available.length?Math.min(...available.map(item=>item.points[item.points.length-1].atMs)):null;
  const hasWindow=commonStart!=null&&commonEnd!=null&&commonStart<=commonEnd;
  const visible=hasWindow?available.map(item=>({...item,visiblePoints:item.points.filter(p=>p.atMs>=commonStart&&p.atMs<=commonEnd)})):[];
  for(const item of series){
    const samples=hasWindow?item.points.filter(p=>p.atMs>=commonStart&&p.atMs<=commonEnd):[];
    const first=samples[0],last=samples[samples.length-1],card=el("article",null,"comparison-stat "+item.key);
    const heading=el("div",null,"comparison-stat-head");heading.append(el("strong",item.name),el("span",item.hasHistory?"Alpaca 历史":"轮次快照","comparison-source"));card.append(heading);
    card.append(el("div",last?money(last.equity):"暂无数据","comparison-value"));
    const delta=samples.length>1?last.equity-first.equity:null,change=delta!=null&&first.equity!==0?delta/first.equity:null;
    const changeText=delta==null?"共同时间窗数据不足":(delta>0?"+":"")+money(delta)+"（"+(change>0?"+":"")+pct(change)+"）";
    card.append(el("p","共同区间变化 · "+changeText,"comparison-change"));
    card.append(el("p",last?"观察时间 "+time(last.at)+" · "+(item.hasHistory?samples.length+" 个历史采样点":samples.length+" 个账户快照"):"等待可核对的账户数据","comparison-detail"));
    summary.append(card);
  }
  box.append(summary);
  if(!visible.some(item=>item.visiblePoints.length)){
    box.append(el("p","两个账户目前没有可比较的共同时间窗口。","muted"));return box;
  }

  const ns="http://www.w3.org/2000/svg";
  const s=(tag,attrs={})=>{const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,String(v)));return n;};
  const left=94,right=158,top=28,bottom=54,width=1040,height=370,plotRight=width-right,plotWidth=plotRight-left,plotHeight=height-top-bottom;
  const values=visible.flatMap(item=>[
    ...item.visiblePoints.map(p=>p.equity),
    ...item.snapshots.filter(p=>p.atMs>=commonStart&&p.atMs<=commonEnd).map(p=>p.equity)
  ]);
  const low=Math.min(...values),high=Math.max(...values),pad=Math.max((high-low)*0.18,Math.abs(high)*0.0025,1),min=low-pad,max=high+pad;
  const xAt=t=>commonEnd===commonStart?left+plotWidth/2:left+(t-commonStart)/(commonEnd-commonStart)*plotWidth;
  const y=value=>top+(max-Number(value))/(max-min)*plotHeight;
  const svg=s("svg",{viewBox:`0 0 ${width} ${height}`,role:"img","aria-label":"Claude 与 Codex Paper 账户净值比较，共享美元纵轴和日期横轴"});
  const title=s("title");title.textContent="Claude 与 Codex Paper 账户净值 · 共同时间窗口";svg.append(title);
  const tickMoney=value=>new Intl.NumberFormat("zh-CN",{style:"currency",currency:"USD",maximumFractionDigits:0}).format(value);
  for(let i=0;i<5;i++){
    const fraction=i/4,yy=top+fraction*plotHeight,value=max-fraction*(max-min);
    svg.append(s("line",{x1:left,y1:yy,x2:plotRight,y2:yy,class:"comparison-gridline"}));
    const label=s("text",{x:left-13,y:yy+4,class:"comparison-axis y-axis","text-anchor":"end"});label.textContent=tickMoney(value);svg.append(label);
  }
  const formatDate=value=>new Date(value).toLocaleDateString("zh-CN",{month:"2-digit",day:"2-digit"});
  const dateTicks=commonEnd===commonStart?[commonStart]:[commonStart,(commonStart+commonEnd)/2,commonEnd];
  dateTicks.forEach((value,index)=>{
    const position=xAt(value),label=s("text",{x:position,y:height-17,class:"comparison-axis x-axis","text-anchor":index===0?"start":index===dateTicks.length-1?"end":"middle"});
    label.textContent=formatDate(value);svg.append(s("line",{x1:position,y1:top+plotHeight,x2:position,y2:top+plotHeight+5,class:"comparison-tick"}));svg.append(label);
  });
  for(const item of visible){
    const rows=item.visiblePoints;let segment=[],segmentExperiment=null;
    const flush=()=>{
      if(segment.length>1)svg.append(s("polyline",{points:segment.map(p=>xAt(p.atMs)+","+y(p.equity)).join(" "),class:"equity-line "+item.key}));
      else if(segment.length===1)svg.append(s("circle",{cx:xAt(segment[0].atMs),cy:y(segment[0].equity),r:3.5,class:"equity-sample "+item.key}));
      segment=[];segmentExperiment=null;
    };
    rows.forEach(p=>{
      const id=experimentId(p);
      if(!id){flush();return;}
      if(segment.length&&segmentExperiment!==id)flush();
      if(!segment.length)segmentExperiment=id;
      segment.push(p);
    });flush();
    for(const p of item.snapshots){
      if(p.atMs<commonStart||p.atMs>commonEnd)continue;
      const dot=s("circle",{cx:xAt(p.atMs),cy:y(p.equity),r:4.2,class:"equity-round "+item.key}),tip=s("title");
      tip.textContent=item.name+" 轮次快照 · "+time(p.at)+" · "+money(p.equity)+" · "+(experimentId(p)||"实验版本未记录");dot.append(tip);svg.append(dot);
    }
  }
  const endpointCandidates=visible.map(item=>{const last=item.visiblePoints[item.visiblePoints.length-1];return last?{item,last}:null;}).filter(Boolean)
    .sort((a,b)=>y(a.last.equity)-y(b.last.equity));
  if(endpointCandidates.length===2&&Math.abs(y(endpointCandidates[0].last.equity)-y(endpointCandidates[1].last.equity))<27){
    const center=(y(endpointCandidates[0].last.equity)+y(endpointCandidates[1].last.equity))/2;
    endpointCandidates[0].labelY=Math.max(top+11,center-14);endpointCandidates[1].labelY=Math.min(top+plotHeight-11,center+14);
  }
  for(const {item,last,labelY} of endpointCandidates){
    const yy=y(last.equity),tagY=labelY??yy,g=s("g",{class:"equity-end-label "+item.key});
    g.append(s("line",{x1:plotRight+3,y1:yy,x2:plotRight+12,y2:tagY}));
    g.append(s("circle",{cx:xAt(last.atMs),cy:yy,r:5.5,class:"equity-latest "+item.key}));
    g.append(s("rect",{x:plotRight+13,y:tagY-12,width:132,height:24,rx:5}));
    const label=s("text",{x:plotRight+21,y:tagY+4});label.textContent=item.name+"  "+money(last.equity);g.append(label);svg.append(g);
  }
  box.append(svg);
  const legend=el("div",null,"comparison-legend");
  for(const item of series){const entry=el("span",null,"comparison-legend-item "+item.key);entry.append(el("i"),el("b",item.name));legend.append(entry);}
  legend.append(el("span","线=账户历史；圆点=每轮账户快照","muted"));box.append(legend);
  const sampling=visible.map(item=>item.name+" "+item.visiblePoints.length+" 点"+(item.hasHistory?" · 15 分钟历史":" · 轮次快照")).join("；");
  box.append(el("p","共同观察窗口 "+time(new Date(commonStart).toISOString())+" 至 "+time(new Date(commonEnd).toISOString())+"。"+sampling+"。纵轴为两账户共用的美元范围且不从零开始；实验 ID 缺失或切换时断线。账户净值变化不能单独归因于某一轮或某版策略。","comparison-note muted"));
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
  const name=teamName(team),box=el("article",null,"team "+name.toLowerCase()),head=el("div",null,"team-head");
  head.append(el("h2",name+" 团队"),el("span",team.status==="available"?"档案可读":"等待发布","pill"));box.append(head,el("p","最近发布 "+time(team.published_at)+" · "+(team.rounds?.length||0)+" 轮档案","muted"));
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
    if(signature!==renderedSignature){currentTeams=data.teams||[];$("charts").replaceChildren(chartView(currentTeams));renderFeed(currentTeams);$("teams").replaceChildren(...currentTeams.map(teamView));renderedSignature=signature;}
    $("updated").textContent="页面更新 "+new Date().toLocaleTimeString("zh-CN",{hour12:false});
  }catch(error){$("message").textContent="读取失败："+error.message;}
}
refresh();setInterval(refresh,30000);
