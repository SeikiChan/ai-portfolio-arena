"use strict";
const $ = id => document.getElementById(id);
let state = null, selected = null, filter = "all", view = "gpt-lab", initialized = false, dirty = false, busy = false;
const labels = {validated:"已完成", failed:"失败", running:"运行中", completed:"已完成", partial_failure:"部分失败", interrupted:"已中断"};
const actions = {new:"新建", add:"加仓", hold:"继续持有", trim:"减仓", exit:"退出", watch:"观察"};
// The model names a packet field; the panel prints the value, so no number is retyped on either side.
const fact = (facts, reference) => {const [id,field]=String(reference).split('#'); const value=facts?.[id]?.[field]; return value==null?'数据缺失':(typeof value==='number'?value.toLocaleString('en-US',{maximumSignificantDigits:6}):String(value));};
const money = value => value == null || value === "" || !Number.isFinite(Number(value)) ? "未知" : new Intl.NumberFormat("en-US", {style:"currency",currency:"USD",maximumFractionDigits:2}).format(Number(value));
const time = value => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN", {hour12:false}) : (value || "—");
function element(tag, text, className) {const node=document.createElement(tag); if(text != null) node.textContent=text; if(className) node.className=className; return node;}
function message(text, error=false) {$('message').hidden=false; $('message').textContent=text; $('message').className='message'+(error?' error':'');}
async function api(path, payload) {
  const response=await fetch(path, payload === undefined ? {cache:"no-store"} : {method:"POST",headers:{"Content-Type":"application/json","X-Console-Token":state.csrf_token},body:JSON.stringify(payload)});
  const data=await response.json(); if(!response.ok) throw new Error(data.error || '请求失败'); return data;
}
function prompts() {return {claude:$('claude-prompt').value,gpt:$('gpt-prompt').value};}
function accountCard(name) {
  const a=state.accounts[name], host=$(name+'-account'); host.replaceChildren();
  const heading=element('div',null,'account-heading'); heading.append(element('b',name==='claude'?'C':'G','model-mark '+name+'-mark'));
  const title=element('div'); title.append(element('div',name==='claude'?'Claude':'Codex','model-name'),element('div',a?'ALPACA PAPER · 账户尾号 '+(a.account_suffix || '未知'):'尚未连接账户','account-id')); heading.append(title);
  const good=a && a.status==='connected' && !state.connection_error;
  heading.append(element('span',good?'已连接 · PAPER':state.connection_error?'连接待刷新':a?.status==='partial'?'数据不完整':'连接失败','badge'+(good?'':' bad'))); host.append(heading);
  const metrics=element('div',null,'account-metrics');
  for(const [label,value] of [['账户净值',money(a?.equity)],['可用现金',money(a?.cash)],['持仓股票',a?.positions == null?'未知':String(a.positions.length)+' 只']]) {const cell=element('div',null,'metric'); cell.append(element('label',label),element('strong',value)); metrics.append(cell);} host.append(metrics);
  let foot='等待 Alpaca 返回 · 不使用旧模拟账本补数';
  if(a) {const delta=a.equity!=null&&a.last_equity!=null?a.equity-a.last_equity:null; foot='最近读取 '+time(a.fetched_at)+' · 较上日净值 '+(delta==null?'未知':money(delta)); if(a.errors.length) foot+=' · '+a.errors.map(e=>e.source+': '+e.error).join('；');}
  host.append(element('div',foot,'account-foot'));
}
function renderRecords() {
  const container=$('records'); container.replaceChildren();
  const rows=state.records.filter(r=>filter==='all'||r.provider===filter); $('record-count').textContent=rows.length+' 条';
  if(!rows.length) {container.append(element('p','尚无研究记录。保存指令后开始第一轮。','empty')); return;}
  for(const row of rows) {
    const button=element('button',null,'record'+(selected===row.id?' selected':'')); button.dataset.id=row.id;
    const top=element('div',null,'record-top'); top.append(element('b',row.provider==='claude'?'Claude':'GPT'),element('span',labels[row.status]||row.status,row.status==='validated'?'good':row.status==='failed'?'bad':''));
    button.append(top,element('span',time(row.at),'record-time'),element('span',row.environment==='legacy_shadow'?'旧本地观察 · 非新 Paper 账户':'Alpaca Paper · '+row.model,'legacy'),element('p',row.summary));
    button.onclick=()=>{selected=row.id; renderRecords(); show(view==='compose'?'input':view); loadDetail().catch(e=>message(e.message,true));}; container.append(button);
  }
}
function show(name) {view=name; document.querySelectorAll('.view').forEach(n=>n.hidden=n.id!==name); document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===name)); if(name==='accounts') renderTables(); if(name==='gpt-lab')renderGptLab().catch(e=>message(e.message,true));}
function table(headers,rows) {const wrap=element('div',null,'table-wrap'), t=element('table'), head=element('thead'), hr=element('tr'); headers.forEach(h=>hr.append(element('th',h))); head.append(hr); t.append(head); const body=element('tbody'); rows.forEach(row=>{const tr=element('tr'); row.forEach(v=>tr.append(element('td',v??'—'))); body.append(tr);}); t.append(body);wrap.append(t);return wrap;}
function renderTables() {
  const host=$('broker-tables');host.replaceChildren();
  for(const name of ['claude','gpt']) {const a=state.accounts[name];host.append(element('h3',name==='claude'?'Claude Paper':'GPT Paper'));if(!a){host.append(element('p','账户尚未连接','empty'));continue;}
    host.append(element('p','快照时间：'+time(a.fetched_at),'hint'));
    if(a.positions==null) host.append(element('p','持仓接口未成功返回；数量未知。','empty')); else if(!a.positions.length) host.append(element('p','当前没有持仓。','empty')); else host.append(table(['股票','股数','平均成本','现价','市值','浮动盈亏'],a.positions.map(p=>[p.symbol,p.qty,money(p.avg_entry_price),money(p.current_price),money(p.market_value),money(p.unrealized_pl)])));
    if(a.orders==null) host.append(element('p','委托接口未成功返回；历史未知。','empty')); else if(!a.orders.length) host.append(element('p','Alpaca 尚未返回任何委托。AI 建议不等于成交。','empty')); else host.append(table(['时间','股票','方向','状态','申请股数/金额','已成交股数','成交均价'],a.orders.map(o=>[time(o.submitted_at),o.symbol,o.side,o.status,o.qty??money(o.notional),o.filled_qty,o.filled_avg_price==null?'—':money(o.filled_avg_price)])));
  }
}
async function loadDetail() {
  if(!selected) {['user-prompt','full-prompt','response'].forEach(id=>$(id).textContent='请从左侧选择一轮研究。'); return;}
  const identity=selected, data=await api('/api/record?id='+encodeURIComponent(identity)); if(identity!==selected)return;
  document.querySelectorAll('.record-meta').forEach(n=>n.textContent=(data.provider==='claude'?'Claude':'GPT')+' · '+(data.environment==='legacy_shadow'?'旧本地观察': 'Alpaca Paper')+' · '+time(data.at)+' · '+(labels[data.status]||data.status)+' · '+data.run_id);
  $('user-prompt').textContent=data.user_prompt; $('full-prompt').textContent=data.full_prompt||'正在准备数据，尚未发给模型。';
  $('response').textContent=data.response||data.result.error||'尚未收到模型回复。'; $('stdout').textContent=data.raw_stdout||'暂无原始返回'; $('stderr').textContent=data.raw_stderr||'暂无错误日志';
  const host=$('plan-body');host.replaceChildren();const d=data.result.decision;
  if(!d){host.append(element('p',data.result.error||'该轮没有通过检查的投资建议。','empty'));return;}
  host.append(element('p',d.overview));
  for(const p of d.positions) {const idea=element('article',null,'plan-idea'), h=element('h3',p.symbol);
    h.append(element('span',(actions[p.action]||(p.target_weight>0?'目标配置':'退出 / 观察'))+' '+(p.target_weight*100).toFixed(0)+'%'));
    idea.append(h,element('p',p.thesis));
    if(p.changes_vs_last) idea.append(element('p','相对上轮：'+p.changes_vs_last,'hint'));
    idea.append(element('p','失效条件：'+(p.invalidation?p.invalidation.description+'（'+p.invalidation.kind+' · '+p.invalidation.reference+'）':(p.exit_condition||'本轮未提供'))));
    idea.append(element('p','预期 '+(p.horizon_days??p.expected_holding_days??'—')+' 天，允许提前复核'+(p.confidence==null?'':'；信心 '+(p.confidence*100).toFixed(0)+'%')+'；不表示已经建仓。','hint'));
    if(p.fact_refs?.length) idea.append(element('p','引用数值：'+p.fact_refs.map(r=>r+' = '+fact(data.facts,r)).join(' · '),'hint'));
    (p.evidence_quotes||[]).forEach(q=>idea.append(element('p','原文「'+q.quote+'」— '+q.evidence_id,'hint')));
    idea.append(element('div','引用：'+p.evidence_ids.join(' · '),'refs'));host.append(idea);}
  if(d.macro_claims?.length){host.append(element('h3','宏观说法（均来自新闻报道，非本数据包数据）'));d.macro_claims.forEach(m=>host.append(element('p','• '+m.claim+'　原文「'+m.quote+'」— '+m.evidence_id,'hint')));}
  if(d.injection_flags?.length)host.append(element('h3','模型上报的可疑指令文本'),element('p',d.injection_flags.join(' · '),'hint'));
  if(d.memory_claims?.length){host.append(element('h3','模型自报的未经证据支持的记忆性说法'));d.memory_claims.forEach(m=>host.append(element('p','• '+m,'hint')));}
  host.append(element('h3','现金与数据缺口'),element('p',d.cash_reason)); (d.evidence_gaps||[]).forEach(g=>host.append(element('p','• '+g,'hint')));
}
function buttons() {document.querySelectorAll('#run-claude,#refresh').forEach(b=>b.disabled=busy||!!state?.active);const active=state?.active;$('progress').hidden=!active;if(active)$('progress').textContent='正在运行：'+active.stage+' · '+time(active.started_at)+'。结果将自动出现，不要重复点击。';}
async function reload() {
  const data=await api('/api/state');state=data;
  if(!initialized){$('claude-prompt').value=data.prompts.claude;$('gpt-prompt').value=data.prompts.gpt; initialized=true;}
  accountCard('claude');accountCard('gpt');renderRecords();buttons();if(view==='accounts')renderTables();if(view==='gpt-lab')renderGptLab().catch(e=>message(e.message,true));
  $('rules-text').textContent='共同股票池 '+state.rules.universe.length+' 只 · 最多 '+state.rules.max_positions+' 只正仓位 · 单股目标不超过 '+(state.rules.max_weight*100)+'% · 总目标仓位不超过 '+(state.rules.max_invested*100)+'%。'; $('instructions').textContent=state.research_instructions;
  $('last-update').textContent='面板更新 '+new Date().toLocaleTimeString('zh-CN',{hour12:false})+' · 账户需手动刷新';
  if(state.connection_error)message(state.connection_error,true);
  if(!selected&&data.records.length)selected=data.records[0].id;
  if(selected&&view!=='compose'&&view!=='accounts'&&view!=='gpt-lab')await loadDetail();
}
async function action(work) {busy=true;buttons();try{await work();await reload();}catch(e){message(e.message,true);}finally{busy=false;buttons();}}
async function run(providers){await action(async()=>{const r=await api('/api/run',{providers,prompts:prompts()});dirty=false;message('研究已开始。Prompt 与原始回复会分别保存，当前不会提交 Paper 订单。');selected='alpaca_paper:'+r.id+':'+providers[0];});}
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{show(b.dataset.view);if(!['compose','accounts','gpt-lab'].includes(view))loadDetail().catch(e=>message(e.message,true));});
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('selected',x===b));renderRecords();});
document.querySelectorAll('[data-copy]').forEach(b=>b.onclick=async()=>{try{await navigator.clipboard.writeText($(b.dataset.copy).textContent);message('已复制。');}catch{message('浏览器未允许复制，请直接选中文本复制。',true);}});
$('claude-prompt').addEventListener('input',()=>{dirty=true;});$('gpt-prompt').addEventListener('input',()=>{dirty=true;});
$('save').onclick=()=>action(async()=>{await api('/api/prompts',prompts());dirty=false;message('Claude 与 Codex 的独立任务已保存，将用于下一次自动交易。');});
$('refresh').onclick=()=>action(async()=>{await api('/api/refresh',{});message('两个 Paper 账户已重新读取。');});
$('run-claude').onclick=()=>run(['claude']);
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
reload().catch(e=>message('连接本地服务失败：'+e.message,true));
setInterval(()=>{if(!busy)reload().catch(()=>{$('last-update').textContent='本地服务暂时不可达，当前为上次显示的记录';});},5000);

const independentRound={};
async function renderGptLab() {
  const data=await api('/api/teams');
  const host=$('gpt-lab-content');host.replaceChildren();
  for(const team of data.teams) {
    const teamName=team.engine_id==='codex_engine'?'Codex':'Claude';
    const panel=element('section',null,'team-panel');
    panel.append(element('h3',teamName+' 团队'),element('p',team.note||''),
      element('p','分支：'+(team.branch||'未知')+' · 工作目录：'+(team.workspace||'未知'),'hint'),
      element('p','最近发布：'+time(team.published_at)+' · 执行模式：'+(team.execution_mode||'read_only_research'),'hint'));
    (team.rejected_rounds||[]).forEach(r=>panel.append(element('p','未通过契约的轮次 '+r.round_id+'：'+r.reason,'hint')));
    if(!team.rounds.length){panel.append(element('p','该团队已建立工作区，尚未发布新研究。旧记录不会计入这个实验。','empty'));host.append(panel);continue;}
    const chooser=element('select');chooser.setAttribute('aria-label','选择 '+teamName+' 的 Paper 轮次');
    team.rounds.forEach(r=>{const o=element('option',time(r.completed_at)+' · '+teamName+' · '+(labels[r.status]||r.status));o.value=r.id;chooser.append(o);});
    if(!team.rounds.some(r=>r.id===independentRound[team.team_id]))independentRound[team.team_id]=team.rounds[0].id;
    chooser.value=independentRound[team.team_id];panel.append(chooser);const body=element('div');panel.append(body);
    const display=()=>{independentRound[team.team_id]=chooser.value;const r=team.rounds.find(x=>x.id===independentRound[team.team_id]);body.replaceChildren();
      const account=r.account_observed||{};
      if(r.execution_error)body.append(element('p','执行未完成：'+r.execution_error,'hint'));
      body.append(element('h3','Paper 账户观察值'),element('p','净值 '+money(account.equity)+' · 现金 '+money(account.cash)+' · 可用购买力 '+money(account.buying_power),'hint'));
      const positions=account.positions||[];
      body.append(element('h3','当前持仓'));
      if(positions.length){const list=element('div',null,'table-wrap'),table=element('table'),head=element('tr');
        ['标的','股数','市值','未实现盈亏'].forEach(v=>head.append(element('th',v)));table.append(head);
        positions.forEach(p=>{const row=element('tr');[p.symbol,p.qty,money(p.market_value),money(p.unrealized_pl)].forEach(v=>row.append(element('td',String(v??'—'))));table.append(row);});list.append(table);body.append(list);
      }else body.append(element('p','暂无持仓','empty'));
      body.append(element('h3','本轮委托与成交'));
      const orders=r.broker_orders||[];
      if(orders.length){orders.forEach(o=>{const req=o.request||{};body.append(element('p',(req.side||'').toUpperCase()+' '+(req.qty||'—')+' 股 '+(req.symbol||'—')+' · '+(o.outcome||'unknown')+' · '+(o.broker_status||o.broker_message||o.error||''),'hint'));});}
      else body.append(element('p',r.execution_mode==='not_executed'?'本轮没有执行记录':'本轮没有新委托；以下为券商返回的近期委托','hint'));
      (account.orders||[]).slice(0,30).forEach(o=>body.append(element('p',(o.side||'').toUpperCase()+' '+(o.filled_qty||0)+'/'+(o.qty||'—')+' 股 '+(o.symbol||'—')+' · '+(o.status||'unknown')+' · 均价 '+(o.filled_avg_price||'—'),'hint')));
      body.append(element('h3','本轮用户任务'),element('pre',r.user_prompt,'small-pre'),element('h3','原始回复'),element('pre',r.response||r.error||'没有模型回复','transcript'));
      const details=element('details',null,'rules');details.append(element('summary','查看实际发送的完整 Prompt'),element('pre',r.full_prompt||'尚未调用模型'));body.append(details);
    };
    chooser.onchange=display;display();host.append(panel);
  }
}
