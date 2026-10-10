import json

import pytest
import requests

from .team_exports import read_gpt_export
from .test_console import web


def test_no_foreign_team_or_fallback(tmp_path):
    path=tmp_path/'dashboard.json'
    assert read_gpt_export(path)['status']=='unavailable'
    path.write_text(json.dumps({'schema_version':1,'team_id':'claude','rounds':[]}))
    with pytest.raises(ValueError):read_gpt_export(path)


def test_gpt_cannot_be_run_from_shared_site(web):
    base,_=web
    token=requests.get(base+'/api/state').json()['csrf_token']
    reply=requests.post(base+'/api/run',json={'providers':['gpt'],'prompts':{'gpt':'test','claude':'test'}},headers={'X-Console-Token':token})
    assert reply.status_code==400
    assert 'worktree' in reply.json()['error']


def test_publication_has_no_write_endpoint(web):
    base,_=web
    token=requests.get(base+'/api/state').json()['csrf_token']
    assert requests.post(base+'/api/teams/gpt',json={},headers={'X-Console-Token':token}).status_code==404
