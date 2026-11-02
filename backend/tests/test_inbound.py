"""Shared inbound messages: identity boundaries, non-consuming cursors and durable delivery."""
import json
import time

import httpx
import pytest

from backend import inbound
from backend.auth import Identity
from backend.store import Problem
from backend.tests.test_api import api, headers, setup_attempt  # noqa: F401
from clients import hubcli, hubtools, remotecli


CONNECTION_CODE = 'synthetic_'+'x'*33
CONNECTION_ID = '7d05caa5-e578-4c2a-8887-3d5b557f6a0a'
INSTALL_ID = 'cc4a8541-2661-4eb8-a1d3-a34e913571c0'


def connection_ready(api, monkeypatch, handler):
    svc=api.app.state.inbound
    svc.off=False;svc.url='https://api.tico.dev';svc.key='tin_synthetic_private_key'
    monkeypatch.setattr(inbound,'TRANSPORT',httpx.MockTransport(handler))
    with svc.store.transaction() as c:
        c.execute("INSERT OR REPLACE INTO registry_metadata VALUES('usage-count',?)",
                  (json.dumps({'install_id':INSTALL_ID}),))
    return svc


def connection_answer(action):
    result={'connected':action=='connect','scope':'inbox','company':{'company_id':CONNECTION_ID,'name':'Acme'}}
    if action=='preview':result['expires_at']=time.time()+300
    return result


def connection_reply(status,body,**kwargs):
    return httpx.Response(status,stream=httpx.ByteStream(json.dumps(body).encode()),**kwargs)


def test_connection_requires_actual_direct_human_management_before_secret_parsing(api,monkeypatch):
    from dataclasses import replace
    from backend.tests.test_api import as_member
    calls=[]
    svc=connection_ready(api,monkeypatch,lambda request:calls.append(request))
    path='/api/v2/inbound/company-connection'
    assert api.post(path,content=b'invalid JSON').status_code==401
    assert api.post(path,headers=headers('cara-test'),content=b'invalid JSON').status_code==403
    owner=Identity('human:ana','owner','ana@acme.example')
    for changes in ({'via':'assistant','confirmed':True},{'via':'botops'},{'via_token':True},
                    {'support':'full'},{'task_actor':'bot:ops'},{'runner_id':'r_test'},
                    {'attempt_id':'a_test'},{'agent':'external'}):
        with pytest.raises(Problem) as denied:svc.connection_actor(replace(owner,**changes))
        assert denied.value.status==403
    # A cached or forged owner role never substitutes for current stored company authority.
    with pytest.raises(Problem):svc.connection_actor(Identity('human:cara','owner','ana@acme.example'))
    assert svc.connection_actor(owner)['actor']=='human:ana'  # loopback owner need not have via_proxy
    assert svc.connection_actor(Identity('human:ben','human','ben@acme.example'))['actor']=='human:ben'
    with svc.store.transaction() as c:
        roster=json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        next(p for p in roster['people'] if p['id']=='ben')['sign_in']=False
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'",(json.dumps(roster),))
    with pytest.raises(Problem):svc.connection_actor(Identity('human:ben','owner','ben@acme.example'))
    as_member(api,'ben@acme.example')
    assert api.post(path,headers=headers('ben-test'),json={'action':'connect','code':CONNECTION_CODE}).status_code==403
    assert calls==[]


def test_connection_validation_and_missing_identity_never_persist_or_echo_code(api,monkeypatch,caplog):
    calls=[]
    svc=connection_ready(api,monkeypatch,lambda request:calls.append(request))
    path='/api/v2/inbound/company-connection'
    assert len(CONNECTION_CODE)==43
    valid=json.dumps({'action':'preview','code':CONNECTION_CODE})
    malformed=[valid[:-1],valid[:-1]+',"code":"'+CONNECTION_CODE+'"}',
               json.dumps({'action':'preview','code':CONNECTION_CODE,CONNECTION_CODE:'unknown'}),
               json.dumps({'action':'preview','code':CONNECTION_CODE+'\n'}),
               json.dumps({'action':'preview','code':{'secret':CONNECTION_CODE}}),
               json.dumps({'action':'other','code':CONNECTION_CODE}),valid+' '*2048]
    for body in malformed:
        response=api.post(path,headers={**headers(),'Content-Type':'application/json'},content=body)
        assert response.status_code==422
        assert response.json()['error']['code']=='inbox_connection_invalid'
        assert CONNECTION_CODE not in response.text
    with svc.store.transaction() as c:c.execute("DELETE FROM registry_metadata WHERE key='usage-count'")
    response=api.post(path,headers=headers(),json={'action':'preview','code':CONNECTION_CODE})
    assert response.status_code==409 and CONNECTION_CODE not in response.text
    with svc.store.read() as c:
        assert c.execute("SELECT 1 FROM registry_metadata WHERE key='usage-count'").fetchone() is None
        assert CONNECTION_CODE not in '\n'.join(c.iterdump())
    with svc.store.transaction() as c:c.execute("INSERT INTO registry_metadata VALUES('usage-count','{corrupt')")
    assert api.post(path,headers=headers(),json={'action':'connect','code':CONNECTION_CODE}).status_code==409
    with svc.store.read() as c:
        assert c.execute("SELECT value FROM registry_metadata WHERE key='usage-count'").fetchone()[0]=='{corrupt'
    assert CONNECTION_CODE not in caplog.text and calls==[]


def test_connection_preview_connect_fixed_transport_and_fresh_role(api,monkeypatch,caplog):
    from backend.tests.test_api import as_member
    calls=[]
    def hq(request):
        body=json.loads(request.content);calls.append(body)
        assert request.url==httpx.URL('https://api.tico.dev/v1/inbound/company-connection')
        assert request.headers['authorization']=='Bearer tin_synthetic_private_key'
        assert request.headers['accept-encoding']=='identity'
        assert set(body)=={'action','code','reported_install_id','actor'}
        assert body['reported_install_id']==INSTALL_ID and body['code']==CONNECTION_CODE
        return connection_reply(200,connection_answer(body['action']))
    svc=connection_ready(api,monkeypatch,hq)
    path='/api/v2/inbound/company-connection'
    for action in ('preview','connect','connect'):
        response=api.post(path,headers=headers('ben-test'),json={'action':action,'code':CONNECTION_CODE})
        assert response.status_code==200,response.text
        assert response.json()['connected'] is (action=='connect')
        assert calls[-1]['actor']=='human:ben'
        assert CONNECTION_CODE not in response.text
    as_member(api,'ben@acme.example')
    assert api.post(path,headers=headers('ben-test'),json={'action':'connect','code':CONNECTION_CODE}).status_code==403
    assert len(calls)==3
    with svc.store.read() as c:assert CONNECTION_CODE not in '\n'.join(c.iterdump())
    assert CONNECTION_CODE not in caplog.text


def test_connection_bounds_upstream_response_and_refuses_redirects_and_false_success(api,monkeypatch):
    calls=[];closed=[];chunks=[]
    class Large(httpx.AsyncByteStream):
        async def __aiter__(self):
            for n in range(10):chunks.append(n);yield b'x'*1024
        async def aclose(self):closed.append(True)
    replies=[lambda:httpx.Response(200,stream=Large()),
             lambda:connection_reply(302,{},headers={'location':'https://elsewhere.example/'+CONNECTION_CODE}),
             lambda:connection_reply(200,{},headers={'content-encoding':'gzip'}),
             lambda:connection_reply(200,{**connection_answer('preview'),'key':CONNECTION_CODE}),
             lambda:connection_reply(200,{**connection_answer('preview'),'connected':True}),
             lambda:connection_reply(409,{'error':'inbox_connection_expired','detail':CONNECTION_CODE})]
    def hq(request):calls.append(str(request.url));return replies.pop(0)()
    svc=connection_ready(api,monkeypatch,hq)
    for i in range(6):
        response=api.post('/api/v2/inbound/company-connection',headers=headers(),json={'action':'preview','code':CONNECTION_CODE})
        assert response.status_code==(409 if i==5 else 503)
        assert CONNECTION_CODE not in response.text
    assert len(calls)==6 and len(chunks)==5 and closed
    svc.url='https://elsewhere.example'
    assert api.post('/api/v2/inbound/company-connection',headers=headers(),json={'action':'preview','code':CONNECTION_CODE}).status_code==503
    assert len(calls)==6

    # A peer that never finishes a body is cancelled and closed by the overall deadline.
    import asyncio
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b'{}'
        async def aclose(self):closed.append('cancelled')
    monkeypatch.setattr(inbound,'CONNECTION_TIMEOUT',.1)
    monkeypatch.setattr(inbound,'TRANSPORT',httpx.MockTransport(lambda request:httpx.Response(200,stream=Slow())))
    svc.url='https://api.tico.dev'
    response=api.post('/api/v2/inbound/company-connection',headers=headers(),json={'action':'preview','code':CONNECTION_CODE})
    assert response.status_code==503 and response.json()['error']['retryable'] is True
    assert 'cancelled' in closed and CONNECTION_CODE not in response.text


@pytest.fixture(autouse=True)
def no_live_inbound(monkeypatch):
    monkeypatch.delenv('TICO_INBOUND_KEY',raising=False)
    monkeypatch.setenv('TICO_INBOUND_URL','https://hq.example.test')
    # Isolate before the API fixture starts: replacing tick later cannot stop an already-submitted callback.
    # These contract tests exercise sync/tick explicitly, with no concurrent timer competing for their state.
    async def idle_loop(self,stop):
        await stop.wait()
    monkeypatch.setattr(inbound.Inbound,'loop',idle_loop)
    # These tests invoke sync/tick explicitly; the app's real timer must not race their fake HQ.
    async def no_background_sync(self,stop):pass
    monkeypatch.setattr(inbound.Inbound,'loop',no_background_sync)


@pytest.fixture
def retention_api(monkeypatch, request):
    # This test drives real ticks explicitly; the app's autonomous worker must
    # not race those ticks or observe its temporary clock and remote fake.
    async def manual_ticks_only(self, stop):
        await stop.wait()
    monkeypatch.setattr(inbound.Inbound, 'loop', manual_ticks_only)
    return request.getfixturevalue('api')


def seed(api):
    svc=api.app.state.inbound
    with svc.store.transaction() as c:
        for ident in ('ibx_a','ibx_b','ibx_c'):
            inbound.save_inbox(c,{'inbox_id':ident,'kind':'email','name':ident,'address':ident+'@example.test',
                                 'state':'active','created_by':'human:ana','period_end':None,'cancel_pending':0})
    return svc


def page(seq=1,inbox='ibx_b',ident='ses:msg:ibx_b',body='Your verification code is 123456.'):
    return {'deliveries':[{'hq_seq':seq,'delivery_id':ident,'inbox_id':inbox,'kind':'email','received_at':time.time(),
                            'from':'Login <login@example.test>','subject':'Sign in','text':body}], 'gaps':[],'cursor':seq}


def test_company_members_share_non_consuming_reads_and_waits(api):
    svc=seed(api);svc.ingest(page())
    for caller in ('ana-test','cara-test'):
        h=headers(caller)
        listed=api.get('/api/v2/inbound/messages?inbox_ids=ibx_a,ibx_b',headers=h)
        assert listed.status_code==200 and len(listed.json()['messages'])==1
        assert '123456' not in listed.json()['messages'][0].keys()
        body=api.get('/api/v2/inbound/messages/ses:msg:ibx_b',headers=h).json()
        assert body['untrusted'] and '123456' in body['text']
        assert api.get('/api/v2/inbound/messages/ses:msg:ibx_b/code',headers=h).json()['code']=='123456'
        waited=api.post('/api/v2/inbound/wait',headers=h,json={'inbox_ids':['ibx_a','ibx_b'],'after':0,'timeout':0}).json()
        assert len(waited['messages'])==1
    assert api.get('/api/v2/inbound/messages?inbox_ids=ibx_a,unknown',headers=headers()).status_code==404
    assert api.get('/api/v2/inbound/messages?inbox_ids=ibx_c',headers=headers()).json()['messages']==[]
    # A 100 KB body is paged, including multibyte text, with no markup rendering.
    text='é'*60000;svc.ingest(page(2,ident='ses:large:ibx_b',body=text))
    data=api.get('/api/v2/inbound/messages/ses:large:ibx_b',headers=headers()).json()
    assert len(data['text'].encode())<=32768 and data['next_offset']
    assert len(api.get('/api/v2/inbound/messages',headers=headers()).content)<96*1024


def test_authorization_rejects_bare_runners_support_and_non_admin_phone_writes(api):
    svc=seed(api)
    for who in (Identity('runner:x','runner'),Identity('service:x','service',scope='tasks'),
                Identity('human:tico-support','human',support='full')):
        with pytest.raises(Problem):svc.list_inboxes(who)
    assert api.post('/api/v2/inbound/inboxes',headers=headers('cara-test'),json={'kind':'sms','name':'Login'}).status_code==403
    assert api.post('/api/v2/inbound/inboxes/ibx_a/release',headers=headers('cara-test'),json={}).status_code==403
    assert api.get('/api/v2/inbound/inboxes').status_code==401
    # Current non-admin members can create email, with the creator bound to the authenticated identity.
    calls=[]
    def remote(method,path,body=None):
        calls.append(body)
        return {'inbox_id':'ibx_new','kind':'email','name':'Login','address':'agent-acme-login@tico.team',
                'state':'active','created_by':body['created_by'],'period_end':None,'cancel_pending':0}
    svc.remote=remote
    result=api.post('/api/v2/inbound/inboxes',headers=headers('cara-test'),json={'kind':'email','name':'Login'})
    assert result.status_code==200 and calls[-1]['created_by']=='human:cara'
    assert 'account_id' not in calls[-1]


def test_commit_before_ack_lost_ack_and_wait_cursor_do_not_lose_messages(api,monkeypatch):
    svc=seed(api);svc.off=False
    ack=[]
    def remote(method,path,body=None):
        if path=='inboxes':return {'inboxes':[]}
        if path=='pull':return page() if body['cursor']==0 else {'deliveries':[],'gaps':[],'cursor':1}
        if path=='ack':
            ack.append(body['through_hq_seq'])
            if len(ack)==1:raise Problem('inbound_unavailable','Lost ack',503)
            return {'ok':True}
    svc.remote=remote
    with pytest.raises(Problem):svc.sync()
    svc.sync()
    with svc.store.read() as c:
        assert c.execute('SELECT count(*) FROM inbound_messages').fetchone()[0]==1
        assert c.execute('SELECT cursor FROM inbound_sync').fetchone()[0]==1
    assert ack==[1,1]
    svc.ingest(page(2,inbox='ibx_c',ident='ses:other:ibx_c'))
    empty=api.post('/api/v2/inbound/wait',headers=headers(),json={'after':1,'inbox_ids':['ibx_b'],'timeout':0}).json()
    assert empty['messages']==[] and empty['cursor']==2
    svc.ingest(page(3,ident='ses:later:ibx_b'))
    for _ in range(2):
        got=api.post('/api/v2/inbound/wait',headers=headers(),json={'after':2,'inbox_ids':['ibx_b'],'timeout':0}).json()
        assert len(got['messages'])==1
    # No content or code is copied into the audit log.
    with svc.store.read() as c:
        events=[dict(r) for r in c.execute("SELECT * FROM events WHERE action LIKE 'inbound.%'")]
    assert '123456' not in json.dumps(events)


def test_shared_names_sync_from_hq_without_losing_local_dependency_notes(api):
    svc=seed(api);calls=[]
    with svc.store.read() as c:
        original=dict(c.execute("SELECT * FROM inbound_inboxes WHERE inbox_id='ibx_a'").fetchone())
    shared={**original,'name':'Shared name'}
    def remote(method,path,body=None):
        calls.append((method,path,body))
        if path.endswith('/rename'):return {**shared,'name':body['name']}
        if path=='inboxes':return {'inboxes':[shared]}
        if path=='pull':return {'deliveries':[],'gaps':[],'cursor':0}
        if path=='ack':return {'ok':True}
    svc.remote=remote
    result=api.patch('/api/v2/inbound/inboxes/ibx_a',headers=headers('cara-test'),
                     json={'name':'Workspace name','used_for':'Recovery for accounting'}).json()
    assert result['name']=='Workspace name' and result['used_for']=='Recovery for accounting'
    assert calls[0]==('POST','inboxes/ibx_a/rename',{'name':'Workspace name','created_by':'human:cara'})
    svc.off=False;svc.sync()
    got=api.get('/api/v2/inbound/inboxes',headers=headers()).json()['inboxes'][0]
    assert got['name']=='Shared name' and got['used_for']=='Recovery for accounting'
    assert api.post('/api/v2/inbound/inboxes/ibx_a/release',headers=headers(),json={}).status_code==409
    renamed=api.patch('/api/v2/inbound/inboxes/ibx_a',headers=headers(),json={'name':'Another name'}).json()
    assert renamed['name']=='Another name' and renamed['used_for']=='Recovery for accounting'
    def unavailable(*args,**kwargs):raise Problem('inbound_unavailable','Unavailable',503)
    svc.remote=unavailable
    failed=api.patch('/api/v2/inbound/inboxes/ibx_a',headers=headers(),json={'name':'Unconfirmed','used_for':''})
    assert failed.status_code==503
    got=api.get('/api/v2/inbound/inboxes',headers=headers()).json()['inboxes'][0]
    assert got['name']=='Another name' and got['used_for']=='Recovery for accounting'
    notes=api.patch('/api/v2/inbound/inboxes/ibx_a',headers=headers(),json={'name':'Another name','used_for':'Recovery and invoices'}).json()
    assert notes['name']=='Another name' and notes['used_for']=='Recovery and invoices'


def test_phone_requests_bind_admin_identity_and_never_buy_numbers(api):
    svc=seed(api);calls=[]
    with svc.store.transaction() as c:
        c.execute("UPDATE inbound_inboxes SET kind='sms' WHERE inbox_id='ibx_c'")
    request={'request_id':'prq_1','name':'Logins','state':'requested','price_micros':None}
    def remote(method,path,body=None):
        calls.append((method,path,body))
        if method=='GET':return {'phone_requests':[request],'next':None}
        return {'phone_request':request}
    svc.remote=remote
    paths=('/api/v2/inbound/phone-requests','/api/v2/inbound/phone-requests/prq_1/accept',
           '/api/v2/inbound/phone-requests/prq_1/cancel')
    for path,body in zip(paths,({'name':'Logins'},{'price_micros':14000000},{})):
        assert api.post(path,headers=headers('cara-test'),json=body).status_code==403
    assert api.get(paths[0],headers=headers('cara-test')).status_code==403
    assert api.patch('/api/v2/inbound/inboxes/ibx_c',headers=headers('cara-test'),json={'name':'Unauthorized'}).status_code==403
    with pytest.raises(Problem):svc.phone_requests(Identity('bot:ops','bot',agent='hermes'))
    assert calls==[]
    assert api.post('/api/v2/inbound/inboxes',headers=headers(),json={'kind':'sms','name':'Logins'}).status_code==409
    assert calls==[]
    assert api.post(paths[0],headers=headers(),json={'name':'Logins','created_by':'human:other'}).status_code==422
    assert api.post(paths[0],headers=headers(),json={'name':'Logins'}).status_code==200
    assert calls[-1][0:2]==('POST','phone-requests')
    assert calls[-1][2]['created_by']=='human:ana' and len(calls[-1][2]['operation_id'])>=8
    assert api.post(paths[1],headers=headers(),json={'price_micros':14000000}).status_code==200
    assert calls[-1]==('POST','phone-requests/prq_1/accept',{'created_by':'human:ana','price_micros':14000000})
    assert api.post(paths[2],headers=headers(),json={}).status_code==200
    assert calls[-1]==('POST','phone-requests/prq_1/cancel',{'created_by':'human:ana'})
    assert api.get(paths[0]+'?limit=1&after=prq_0',headers=headers()).json()['phone_requests']==[request]
    assert calls[-1]==('GET','phone-requests?after=prq_0&limit=1',None)
    assert not any('inboxes/sms' in call[1] for call in calls)


def test_cli_and_mcp_reach_the_same_inbound_tools(monkeypatch):
    class API:
        def get(self,path,**query):return {'path':path,'query':query}
        def post(self,path,body,key=None):return {'path':path,'body':body}
    api=API()
    monkeypatch.setattr(remotecli,"Client",lambda *a,**kw:api)
    monkeypatch.setenv("HUB_API_URL","https://hub.example.test")
    for command in [['inbound','inboxes'],['inbound','create-email','Login'],
                    ['inbound','list','--inbox-id','ibx_a','--inbox-id','ibx_b'],
                    ['inbound','search','code'],['inbound','cursor'],['inbound','wait','--after','0','--timeout','0'],
                    ['inbound','read','ses:msg:ibx_b'],['inbound','code','ses:msg:ibx_b']]:
        args=hubcli.parser().parse_args(command)
        name=remotecli.tool_name(args.fn)
        assert name in hubtools.BY_NAME
        result=remotecli.run(args)
        assert result['path'].startswith('inbound/')


def test_native_and_registered_agents_share_inboxes_but_lose_access_when_paused(api):
    svc=seed(api);svc.ingest(page())
    runner,message,attempt=setup_attempt(api)
    native=api.get('/api/v2/inbound/messages',headers=headers(attempt['token']))
    assert native.status_code==200 and len(native.json()['messages'])==1
    external=Identity('bot:ops','bot',agent='hermes')
    with svc.store.transaction() as c:
        c.execute("INSERT INTO agents(bot,harness,token_hash,created_at,created_by) VALUES('ops','hermes','synthetic',?,'human:ana')",
                  (inbound.H.now(),))
    assert len(svc.list_inboxes(external)['inboxes'])==3
    with svc.store.transaction() as c:c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
    with pytest.raises(Problem):svc.list_inboxes(external)
    assert api.get('/api/v2/inbound/messages',headers=headers(attempt['token'])).status_code==409


@pytest.mark.parametrize('outage',[False,True],ids=['key_absent','hq_outage'])
def test_bodies_and_codes_expire_at_90_days_without_hq(retention_api,monkeypatch,outage):
    api=retention_api
    svc=seed(api);start=1_800_000_000.0
    monkeypatch.setattr(inbound,'now',lambda:start)
    old=page(1,ident='ses:old:ibx_b');old['deliveries'][0]['received_at']=start
    svc.ingest(old)
    calls=[]
    if outage:
        svc.off=False
        def remote(method,path,body=None):
            calls.append(path);raise Problem('inbound_unavailable','HQ down',503)
        svc.remote=remote
    else:
        assert svc.off and not svc.key
    def listed(q=''):
        return api.get('/api/v2/inbound/messages'+('?q='+q if q else ''),headers=headers()).json()['messages']
    # One second before the cutoff the content is readable.
    monkeypatch.setattr(inbound,'now',lambda:start+inbound.RETENTION-1)
    assert api.get('/api/v2/inbound/messages/ses:old:ibx_b/code',headers=headers()).json()['code']=='123456'
    assert listed('verification')[0]['has_code']
    # Past the cutoff reads refuse before any purge or HQ call, and listing masks preview and code.
    monkeypatch.setattr(inbound,'now',lambda:start+inbound.RETENTION+1)
    rows=listed()
    assert rows[0]['retention_removed'] and rows[0]['preview']=='' and not rows[0]['has_code']
    assert listed('verification')==[]
    assert api.get('/api/v2/inbound/messages?has_code=true',headers=headers()).json()['messages']==[]
    for path in ('ses:old:ibx_b','ses:old:ibx_b/code'):
        refused=api.get('/api/v2/inbound/messages/'+path,headers=headers())
        assert refused.status_code==410 and refused.json()['error']['code']=='retention'
    # The background tick purges stored content although sync is off or failing; metadata stays.
    with svc.store.transaction() as c:c.execute('UPDATE inbound_messages SET retention_removed=0,body=?,code=?,preview=?',
                                                ('Your verification code is 123456.','123456','x'))
    svc.purged_at=0
    if outage:
        with pytest.raises(Problem):svc.tick()
        assert calls==['inboxes']
    else:
        svc.tick()
    with svc.store.read() as c:
        row=dict(c.execute('SELECT * FROM inbound_messages').fetchone())
    assert row['body'] is None and row['code'] is None and row['preview']=='' and row['retention_removed']==1
    assert row['sender'] and row['subject']=='Sign in'


def test_search_pages_to_older_matches_with_before(monkeypatch,api):
    svc=seed(api)
    for seq in range(1,6):
        svc.ingest(page(seq,ident=f'ses:m{seq}:ibx_b',body=f'Login code is 12345{seq}.' if seq!=3 else 'Unrelated note'))
    class API:
        def get(self,path,**query):
            return api.get('/api/v2/'+path,params=query,headers=headers()).json()
    monkeypatch.setattr(remotecli,"Client",lambda *a,**kw:API())
    monkeypatch.setenv("HUB_API_URL","https://hub.example.test")
    def cli(*extra):
        return remotecli.run(hubcli.parser().parse_args(['inbound','search','Login','--limit','2',*extra]))
    first=cli()
    second=cli('--before',str(first['next']))
    assert [m['message_id'] for m in first['messages']]==['ses:m5:ibx_b','ses:m4:ibx_b']
    assert [m['message_id'] for m in second['messages']]==['ses:m2:ibx_b','ses:m1:ibx_b'] and second['next'] is None
    # MCP takes the same continuation.
    tool=hubtools.BY_NAME['hub_inbound_search']
    assert 'before' in tool['inputSchema']['properties']
    again=tool['fn'](API(),{'q':'Login','limit':2,'before':first['next']})
    assert again['messages']==second['messages']


def test_wait_transport_budget_is_exact_route_and_valid_integer_only():
    from clients.tico import inbound_wait_timeout
    path='/api/v2/inbound/wait'
    assert inbound_wait_timeout('POST',path,{},30)==35
    for wait,expected in ((0,30),(120,130)):
        assert inbound_wait_timeout('POST',path,{'timeout':wait},30)==expected
    assert inbound_wait_timeout('POST',path,{'timeout':120},200)==200
    assert inbound_wait_timeout('POST',path,{'timeout':120},30,query={'x':1})==30
    for value in (True,False,None,'120',120.0,float('nan'),float('inf'),-1,121):
        assert inbound_wait_timeout('POST',path,{'timeout':value},30)==30
    for method,target,body in (('GET',path,{'timeout':120}),('POST',path+'/',{'timeout':120}),
            ('POST',path+'?x=1',{'timeout':120}),('POST','/api/v2/other',{'timeout':120}),('POST',path,None)):
        assert inbound_wait_timeout(method,target,body,30)==30


def test_stdio_mcp_wait_covers_server_deadline_without_changing_ordinary_timeout(monkeypatch):
    import io
    from clients import hubmcp, tico
    calls=[]
    class VirtualServer:
        def open(self,request,timeout):
            calls.append((request.full_url,timeout))
            if request.full_url.endswith('/me'):
                value={'actor':'bot:ops','kind':'bot'}
            else:
                assert request.full_url.endswith('/inbound/wait')
                assert json.loads(request.data)['timeout']==120
                if timeout<121:raise TimeoutError('synthetic timeout')
                value={'messages':[],'cursor':0,'next':None}
            return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr(tico.urllib.request,'build_opener',lambda *args:VirtualServer())
    monkeypatch.setattr(tico.time,'sleep',lambda _:None)
    monkeypatch.setenv('HUB_API_URL','https://hub.example.test')
    monkeypatch.setenv('HUB_TOKEN','synthetic-lease')
    stdin=io.StringIO(json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
        'name':'hub_inbound_wait','arguments':{'after':0,'timeout':120}}})+'\n')
    stdout=io.StringIO()
    assert hubmcp.main(stdin,stdout)==0
    result=json.loads(stdout.getvalue())['result']
    assert result['isError'] is False and result['structuredContent']['messages']==[]
    assert calls==[('https://hub.example.test/api/v2/me',30),('https://hub.example.test/api/v2/inbound/wait',130)]
    assert 'synthetic-lease' not in stdout.getvalue()


def _http_wait_probe(monkeypatch, *, deadline=False):
    """Real MCP HTTP→Protocol→ASGI call, using a virtual response deadline."""
    import asyncio
    import threading
    from types import SimpleNamespace
    from fastapi import FastAPI,Request
    from fastapi.testclient import TestClient
    from backend import mcp
    app=FastAPI();app.state.auth=SimpleNamespace();entered=threading.Event();cancelled=threading.Event()
    budgets=[];futures=[];actual=mcp.asyncio.run_coroutine_threadsafe
    @app.middleware('http')
    async def identity(request,call_next):
        request.state.identity=SimpleNamespace(role='bot',actor='bot:ops',agent='',via='')
        return await call_next(request)
    @app.post('/api/v2/inbound/wait')
    async def wait(request:Request):
        body=await request.json();entered.set()
        if deadline:
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:cancelled.set();raise
        return {'messages':[],'cursor':body['after'],'next':None}
    @app.get('/api/v2/inbound/cursor')
    async def cursor():return {'cursor':0}
    class Future:
        def __init__(self,raw):self.raw=raw;self.cancel_calls=0
        def result(self,timeout):
            budgets.append(timeout)
            if deadline:
                assert entered.wait(2)
                raise TimeoutError('synthetic timeout')
            result=self.raw.result(5)
            # The first response represents121 virtual seconds. No wall-clock
            # race: scheduler load must not turn a budget regression into a flake.
            if len(budgets)==1 and timeout<121:raise TimeoutError('synthetic timeout')
            return result
        def cancel(self):
            self.cancel_calls+=1
            return self.raw.cancel()
    def schedule(coroutine,loop):
        raw=actual(coroutine,loop)
        if coroutine.__name__!='go':return raw
        future=Future(raw);futures.append(future);return future
    monkeypatch.setattr(mcp.asyncio,'run_coroutine_threadsafe',schedule)
    monkeypatch.setattr(mcp,'record_call',lambda *args:None)
    mcp.install_mcp(app,SimpleNamespace(public_url='https://hub.example.test'))
    try:
        with TestClient(app,raise_server_exceptions=False) as client:
            response=client.post('/api/v2/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call',
                'params':{'name':'hub_inbound_wait','arguments':{'after':0,'timeout':120}}})
            if deadline:assert cancelled.wait(2)
            else:
                ordinary=client.post('/api/v2/mcp',json={'jsonrpc':'2.0','id':2,'method':'tools/call',
                    'params':{'name':'hub_inbound_cursor','arguments':{}}})
                assert ordinary.status_code==200 and not ordinary.json()['result']['isError']
            return response,budgets,[future.cancel_calls for future in futures]
    finally:
        for future in futures:future.raw.cancel()


def test_http_mcp_honors_long_wait_and_keeps_other_request_budget(monkeypatch):
    response,budgets,cancelled=_http_wait_probe(monkeypatch)
    assert response.status_code==200
    assert response.json()['result']['structuredContent']=={'messages':[],'cursor':0,'next':None}
    assert response.json()['result']['isError'] is False
    assert budgets==[135,65] and cancelled==[0,0]


def test_http_mcp_cancels_timed_out_wait_and_returns_typed_retryable_error(monkeypatch):
    response,budgets,cancelled=_http_wait_probe(monkeypatch,deadline=True)
    assert response.status_code==200 and budgets==[135] and cancelled==[1]
    result=response.json()['result']
    assert result['isError'] is True
    assert result['structuredContent']['error']=='unavailable' and result['structuredContent']['retryable'] is True
    assert 'synthetic timeout' not in response.text
