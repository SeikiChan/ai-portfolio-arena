"use strict";
const $=id=>document.getElementById(id);
const money=v=>v==null||!Number.isFinite(Number(v))?"未知":new Intl.NumberFormat("zh-CN",{style:"currency",currency:"USD",maximumFractionDigits:2}).format(Number(v));
const time=v=>v&&!Number.isNaN(Date.parse(v))?new Date(v).toLocaleString("zh-CN",{hour12:false}):(v||"—");
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
function renderTeam(team){
  const panel=el('article',null,'team'),name=team.engine_id==='codex_engine'?'Codex':'Claude';
  const head=el('div',null,'team-head');head.append(el('div',null));head.firstChild.append(el('h2',name+' 团队'),el('div','最近发布 '+time(team.published_at)+' · '+(team.execution_mode||'未知'),'meta'));
  head.append(el('span',team.status==='available'?'有新结果':team.status==='initialized_no_runs'?'尚无交易记录':'读取异常','badge'+(team.status==='available'?'':' bad')));panel.append(head);
  (team.rejected_rounds||[]).forEach(x=>panel.append(el('p','轮次 '+x.round_id+' 未发布：'+x.reason,'muted')));
  if(!team.rounds?.length){panel.append(el('p',team.note||'等待团队首次发布。','empty'));return panel;}
  team.rounds.forEach(r=>{
    const section=el('section',null,'round'),top=el('div',null,'round-top');top.append(el('h3',time(r.completed_at)+' · '+(r.status||'unknown')),el('span','Packet '+(r.packet_sha256||'').slice(0,12)+' · '+(r.execution_mode||'未执行'),'muted'));section.append(top);
    if(r.execution_error)section.append(el('p','执行受阻：'+r.execution_error,'muted'));
    const a=r.account_observed||{},grid=el('div',null,'grid');
    for(const [label,value] of [['账户净值',money(a.equity)],['现金',money(a.cash)],['可用购买力',money(a.buying_power)]]){const box=el('div',null,'metric');box.append(el('label',label),el('strong',value));grid.append(box);}section.append(grid);
    section.append(el('h3','当前持仓'));const positions=a.positions||[];
    if(positions.length){const table=el('table'),row=el('tr');['标的','股数','市值','未实现盈亏'].forEach(x=>row.append(el('th',x)));table.append(row);positions.forEach(p=>{const tr=el('tr');[p.symbol,p.qty,money(p.market_value),money(p.unrealized_pl)].forEach(x=>tr.append(el('td',String(x??'—'))));table.append(tr);});section.append(table);}else section.append(el('p','暂无可发布的持仓快照。','muted'));
    section.append(el('h3','本轮委托'));const orders=r.broker_orders||[];
    if(orders.length){const list=el('ul',null,'orders');orders.forEach(o=>{const q=o.request||{};list.append(el('li',(q.side||'').toUpperCase()+' '+(q.qty||'—')+' 股 '+(q.symbol||'—')+' · '+(o.outcome||'unknown')+' · '+(o.broker_status||o.broker_message||o.error||'')));});section.append(list);}else section.append(el('p',r.execution_mode==='dry_run'?'仅生成 dry-run 计划，没有提交委托。':'本轮没有新委托。','muted'));
    const broker=(a.orders||[]).slice(0,20);if(broker.length){section.append(el('h3','券商近期委托状态'));const list=el('ul',null,'orders');broker.forEach(o=>list.append(el('li',(o.side||'').toUpperCase()+' '+(o.filled_qty||0)+'/'+(o.qty||'—')+' 股 '+(o.symbol||'—')+' · '+(o.status||'unknown')+' · 成交均价 '+(o.filled_avg_price||'—'))));section.append(list);}
    if(r.response){const d=el('details'),summary=el('summary','查看团队原始决策');d.append(summary,el('pre',r.response));section.append(d);}
    panel.append(section);
  });return panel;
}
async function refresh(){try{const response=await fetch('/api/teams',{cache:'no-store'}),data=await response.json();if(!response.ok)throw new Error(data.error||'发布文件暂不可读');$('message').textContent='';const host=$('teams');host.replaceChildren(...(data.teams||[]).map(renderTeam));$('updated').textContent='页面更新 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});}catch(e){$('message').textContent='暂时无法读取团队发布：'+e.message;}}
refresh();setInterval(refresh,5000);
