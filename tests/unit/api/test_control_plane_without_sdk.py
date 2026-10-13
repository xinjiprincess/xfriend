"""A real cold interpreter must serve existing accounts/history without SDKs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_http_control_plane_does_not_import_execution_sdks(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps(
            {"require_setup_password": False, "enable_api_docs": True, "history_v2_enabled": True}
        ),
        encoding="utf-8",
    )
    (tmp_path / "xfriend.db").touch()
    script = r"""
import asyncio, importlib.abc, json, sys
from pathlib import Path
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'octop_harness', 'harness', 'deepagents', 'openai', 'google', 'langgraph'}:
            raise AssertionError('Control plane imported execution SDK: ' + fullname)
sys.meta_path.insert(0, Guard())
import httpx
from langchain_core.messages import HumanMessage, AIMessage, message_to_dict
from octop.api.app import build_app
from octop.infra.server import OctopServer
from octop.infra.db.repos.thread_messages import ThreadMessageInput

async def main():
    home = Path(sys.argv[1])
    server = OctopServer(home)
    await server.start(defer_agents=True)
    try:
        user = await server.user_manager.create(username='existing-user', password='StartupPassword12!', role='user')
        other = await server.user_manager.create(username='other-user', password='OtherPassword12!', role='user')
        repos, threads, archive = server.services, server.thread_registry, server.history_archive
        repos.agent_repo.create(agent_id='expert', name='Existing expert', user_id=user.id)
        repos.agent_repo.set_state('expert', 'running')
        repos.agent_repo.create(agent_id='other-expert', name='Private expert', user_id=other.id)
        repos.agent_repo.create(agent_id='stopped', name='Stopped expert', user_id=user.id)
        repos.agent_repo.set_state('stopped', 'stopped')
        for tid, owner, aid in [('projection',user.id,'expert'),('v2',user.id,'expert'),('checkpoint',user.id,'expert'),('private',other.id,'expert')]:
            threads.create_thread(agent_id=aid,user_id=owner,channel_type='dashboard',session_key=f'expert:dashboard:{owner}:dm',thread_id=tid)
        wires = [message_to_dict(HumanMessage(content='Old question',id='u1')),message_to_dict(AIMessage(content='Old answer',id='a1'))]
        repos.thread_message_repo.replace_all('projection', [ThreadMessageInput(w['data']['id'],w['type'],json.dumps(w),100+i) for i,w in enumerate(wires)])
        repos.thread_message_repo.mark_projection('checkpoint', 'pending')
        turn = archive.begin('expert','v2')
        archive.save_messages(turn, wires)
        archive.finish(turn['id'],'completed')
    finally:
        await server.stop()

    server = OctopServer(home)
    await server.start(defer_agents=True)
    try:
        assert server.app_runtime is None and server.database_bound
        app = build_app(server)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            response = await client.get('/api/setup/status')
            assert response.status_code == 200, response.text
            assert response.json()['setup_required'] is False, response.text
            response = await client.post('/api/auth/login',json={'username':'existing-user','password':'StartupPassword12!'})
            assert response.status_code == 200,response.text
            client.headers['Authorization']='Bearer '+response.json()['access_token']
            for path in ['/api/auth/me','/api/preferences','/api/settings/timezone','/api/settings/upload','/api/providers/resolved','/api/execution/experts']:
                r=await client.get(path)
                assert r.status_code==200,(path,r.text)
            schema=(await client.get('/api/openapi.json')).json()
            assert '/api/agents/{agent_id}/chat/hitl/resume' in schema['paths']
            health=(await client.get('/api/health')).json()
            assert health['core_ready'] and not health['runtime_ready'] and not health['runtime_failed']
            agents=(await client.get('/api/agents')).json()
            byid={row['agent_id']:row for row in agents}
            assert 'other-expert' not in byid
            assert byid['expert']['startup_pending'] and byid['expert']['state']=='starting'
            assert not byid['stopped']['startup_pending'] and byid['stopped']['state']=='stopped'
            rows=(await client.get('/api/agents/expert/threads')).json()
            assert {r['thread_id'] for r in rows}=={'projection','v2','checkpoint'}
            for tid in ['projection','v2']:
                r=await client.get(f'/api/agents/expert/threads/{tid}/history?limit=1')
                assert r.status_code==200,(tid,r.text)
                page=r.json()
                assert len(page['messages'])==1 and page['has_more'],page
                assert not page['turn_active'] and not page['history_loading']
                older=await client.get(f'/api/agents/expert/threads/{tid}/history',params={'limit':1,'cursor':page['next_cursor']})
                assert older.status_code==200 and len(older.json()['messages'])==1,older.text
                preview=await client.get(f'/api/agents/expert/threads/{tid}/document-preview')
                assert preview.status_code==200 and len(preview.json()['messages'])==2,preview.text
                exported=await client.get(f'/api/agents/expert/threads/{tid}/history/export')
                assert exported.status_code==200 and len(exported.json()['messages'])==2,exported.text
                trajectory=await client.get(f'/api/agents/expert/threads/{tid}/trajectory')
                assert trajectory.status_code==200,trajectory.text
            forbidden=await client.get('/api/agents/expert/threads/private/history')
            assert forbidden.status_code==403,forbidden.text
            for method,path,body in [('GET','/api/agents/expert/threads/checkpoint/history',None),('POST','/api/agents/expert/start',{}),('POST','/api/execution/tasks',{'expert_ids':['expert'],'text':'run'})]:
                r=await client.request(method,path,json=body)
                assert r.status_code==503 and r.json()['error']['code']=='SERVICE_STARTING',(path,r.text)
                assert 'setup_required' not in r.json()
            assert server.app_runtime is None
    finally:
        await server.stop()
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
