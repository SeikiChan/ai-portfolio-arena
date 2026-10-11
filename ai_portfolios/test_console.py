import json
import threading
from pathlib import Path

import pytest
import requests

from ai_portfolios.common import write
from ai_portfolios.console_server import create_server
from ai_portfolios.console_store import ConsoleStore
from ai_portfolios.paper_accounts import BASE, load_credentials, model_portfolio, public_account, read_one, verify_bindings


def account(identity='account-a'):
    return {'provider':'claude', 'status':'connected', 'fetched_at':'2026-10-10T01:00:00Z', 'errors':[],
            'account':{'id':identity,'account_number':'PA12345678','equity':'100000','cash':'100000','last_equity':'100000','buying_power':'200000','status':'ACTIVE'},
            'positions':[], 'orders':[]}


def test_credentials_do_not_fall_back_to_existing_account(tmp_path):
    p=tmp_path/'keys.env'
    p.write_text('APCA_API_KEY_ID=old\nAPCA_API_SECRET_KEY=old-secret',encoding='utf-8')
    with pytest.raises(ValueError,match='尚未完整'):
        load_credentials(p)


def test_credential_mapping_and_duplicate_rejection(tmp_path):
    p=tmp_path/'keys.env'
    p.write_text('ClaudeAPCA_API_KEY_ID=c\nClaudeAPCA_API_SECRET_KEY=cs\nGptAPCA_API_KEY_ID=g\nGptAPCA_API_SECRET_KEY=gs',encoding='utf-8')
    assert load_credentials(p)=={'claude':('c','cs'),'gpt':('g','gs')}
    p.write_text(p.read_text().replace('KEY_ID=g','KEY_ID=c'),encoding='utf-8')
    with pytest.raises(ValueError,match='相同'):
        load_credentials(p)


def test_duplicate_broker_identity_rejected():
    with pytest.raises(ValueError,match='同一个'):
        verify_bindings({'claude':account(),'gpt':account()})


def test_bound_account_cannot_silently_change():
    with pytest.raises(ValueError,match='身份改变'):
        verify_bindings({'claude':account('new')},{'claude':'old'})


def test_missing_response_does_not_replace_pinned_identity():
    assert verify_bindings({'claude':{'status':'unavailable'}},{'claude':'old'})=={'claude':'old'}


def test_paper_only_gets_and_no_credentials_in_records(tmp_path):
    calls=[]
    class Response:
        status_code=200
        def __init__(self,value): self.value=value
        def json(self): return self.value
    def get(url,**kwargs):
        calls.append((url,kwargs))
        return Response(account()['account'] if url.endswith('/account') else [])
    data=read_one('claude',('key-secret','secret-secret'),tmp_path,get)
    assert data['status']=='connected'
    assert len(calls)==3
    assert all(url.startswith(BASE+'/v2/') and kwargs['allow_redirects'] is False for url,kwargs in calls)
    assert 'secret-secret' not in json.dumps(data)
    assert all('secret-secret' not in p.read_text() for p in tmp_path.iterdir())


def test_failed_holdings_is_unknown_not_empty(tmp_path):
    class Response:
        status_code=403
    result=read_one('claude',('key','secret'),tmp_path,lambda *a,**k:Response())
    assert result['status']=='unavailable'
    assert public_account(result)['positions'] is None


def test_public_view_masks_account_identity():
    public=public_account(account())
    assert public['account_suffix']=='5678'
    assert 'account-a' not in json.dumps(public)
    assert 'PA12345678' not in json.dumps(public)


def test_model_context_has_no_account_identifier():
    portfolio=model_portfolio(account(),None)
    assert portfolio['cash']==100000
    assert 'account-a' not in json.dumps(portfolio)


def test_model_input_rejects_short_positions():
    a=account();a['positions']=[{'symbol':'AAPL','qty':'-1','current_price':'100'}]
    with pytest.raises(ValueError): model_portfolio(a,None)


def test_prompt_edits_preserve_old_transcript(tmp_path):
    store=ConsoleStore(tmp_path/'console',tmp_path/'legacy')
    directory=store.root/'rounds'/'20261010T010000Z'
    write(directory/'round.json',{'status':'completed','providers':['gpt'],'user_prompts':{'gpt':'old mandate'}})
    write(directory/'gpt_status.json',{'status':'validated','provider':'gpt'})
    (directory/'gpt_prompt.txt').write_text('old exact full input',encoding='utf-8')
    store.save_prompts({'gpt':'new mandate','claude':'another mandate'})
    detail=store.detail('alpaca_paper:20261010T010000Z:gpt')
    assert detail['user_prompt']=='old mandate'
    assert detail['full_prompt']=='old exact full input'
    assert store.prompts()['gpt']=='new mandate'


def test_detail_cannot_read_arbitrary_files(tmp_path):
    store=ConsoleStore(tmp_path/'console',tmp_path/'legacy')
    with pytest.raises(ValueError):store.detail('../../.env.aitrading')


def test_legacy_records_not_paper_holdings(tmp_path):
    store=ConsoleStore(tmp_path/'console',tmp_path/'legacy')
    directory=store.legacy/'runs'/'20261009T010000Z'
    write(directory/'claude_status.json',{'status':'failed','provider':'claude'})
    (directory/'claude_stdout.txt').write_text(json.dumps({'result':'OAuth expired'}),encoding='utf-8')
    row=store.records()[0]
    assert row['environment']=='legacy_shadow'
    assert store.detail(row['id'])['response']=='OAuth expired'
    assert store._last_decision('claude') is None


@pytest.fixture
def web(tmp_path):
    store=ConsoleStore(tmp_path/'console',tmp_path/'legacy')
    server=create_server(store,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}',store
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def test_http_page_and_csrf(web):
    base,store=web
    assert requests.get(base).status_code==200
    payload={'claude':'valid c','gpt':'valid g'}
    assert requests.post(base+'/api/prompts',json=payload).status_code==403
    token=requests.get(base+'/api/state').json()['csrf_token']
    assert requests.post(base+'/api/prompts',json=payload,headers={'X-Console-Token':token,'Origin':'https://evil.example'}).status_code==403
    assert requests.post(base+'/api/prompts',json=payload,headers={'X-Console-Token':token,'Origin':base}).status_code==200
    assert store.prompts()==payload


def test_http_rejects_rebinding_and_files(web):
    base,_=web
    assert requests.get(base+'/api/state',headers={'Host':'evil.example'}).status_code==403
    assert requests.get(base+'/.env.aitrading').status_code==404
    assert requests.get(base+'/api/record',params={'id':'../../.env'}).status_code==400


def test_no_submit_or_cancel_endpoint(web):
    base,_=web
    token=requests.get(base+'/api/state').json()['csrf_token']
    assert requests.post(base+'/api/orders',json={},headers={'X-Console-Token':token}).status_code==404


def test_model_failure_round_is_not_still_running(tmp_path):
    store=ConsoleStore(tmp_path/'console',tmp_path/'legacy')
    directory=store.root/'rounds'/'20261010T010000Z'
    write(directory/'round.json',{'status':'failed','providers':['claude'],'stage':'运行中断'})
    write(directory/'claude_status.json',{'status':'running'})
    assert store.records()[0]['status']=='failed'


def test_out_of_universe_holdings_never_reach_model(tmp_path, monkeypatch):
    import ai_portfolios.console_store as module
    store = ConsoleStore(tmp_path/'console', tmp_path/'legacy')
    a = account()
    a['positions'] = [{'symbol':'OUTSIDE_UNIVERSE','qty':'1','current_price':'100'}]
    monkeypatch.setattr(store, '_capture', lambda: ({'snapshot_id':'test','accounts':{'claude':a}}, {'gpt':('k','s')}))
    def claim(config, directory, packet_id=None, **kwargs):
        write(directory/'packet.json', {'snapshot_id':'test'})
        return {'packet_id':'p1','packet_sha256':'h','frozen_at':'2026-10-10T00:00:00+00:00','age_seconds':1.0}
    monkeypatch.setattr(module.packet_broker, 'claim', claim)
    calls = []
    monkeypatch.setattr(module, 'run_model', lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(module, 'external_reference', lambda: {'snapshot_id':'test'})
    directory = store.root/'rounds'/'test'
    directory.mkdir(parents=True)
    job = {'id':'test','providers':['claude'],'status':'running','user_prompts':{'claude':'test'}}
    store.operation.acquire()
    store._run(directory, job)
    assert calls == []
    result = json.loads((directory/'claude_status.json').read_text(encoding='utf-8'))
    assert result['status'] == 'failed'
    assert '股票池之外' in result['error']
    assert not store.operation.locked()
