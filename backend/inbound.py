"""Company inbound inboxes. Shared by people and agents; reads never consume messages.

HQ owns supplier credentials. This install holds its own inbound key and pulls normalized text only.
"""
import asyncio
import json
import os
import re
import time
import uuid
from typing import Literal
from urllib.parse import quote, urlencode, urlparse

import httpx
from fastapi import Query, Request
from pydantic import Field

from clients.gateway import user_agent

from .auth import validate_identity
from .models import Contract
from .store import H, Problem, encode

TRANSPORT = None  # tests' fake HQ, never a live company database
RETENTION = 90*86400  # bodies and codes; metadata stays
PURGE_EVERY = 600  # reads refuse expired content before the next purge
now = time.time  # tests' controlled clock
SCHEMA = '''
CREATE TABLE IF NOT EXISTS inbound_inboxes(
 inbox_id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, address TEXT NOT NULL,
 state TEXT NOT NULL, created_by TEXT NOT NULL, period_end REAL, cancel_pending INTEGER NOT NULL DEFAULT 0,
 used_for TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS inbound_messages(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE, inbox_id TEXT NOT NULL,
 kind TEXT NOT NULL, received_at REAL NOT NULL, sender TEXT NOT NULL, subject TEXT NOT NULL,
 preview TEXT NOT NULL, body TEXT, code TEXT, spam INTEGER NOT NULL DEFAULT 0, truncated INTEGER NOT NULL DEFAULT 0,
 retention_removed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS inbound_messages_inbox ON inbound_messages(inbox_id,seq);
CREATE INDEX IF NOT EXISTS inbound_messages_retention ON inbound_messages(retention_removed,received_at);
CREATE TABLE IF NOT EXISTS inbound_sync(
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), cursor INTEGER NOT NULL DEFAULT 0,
 last_sync REAL, error TEXT NOT NULL DEFAULT '', gaps_json TEXT NOT NULL DEFAULT '[]');
INSERT OR IGNORE INTO inbound_sync(singleton) VALUES(1);
'''


class Create(Contract):
    kind: Literal['email','sms'] = 'email'
    name: str = Field(default='Agent inbox',min_length=1,max_length=80)
    max_price_micros: int = Field(default=0,ge=0)


class Edit(Contract):
    name: str = Field(min_length=1,max_length=80)
    used_for: str | None = Field(default=None,max_length=1000)


class Release(Contract):
    confirm: str = Field(default='',max_length=160)


class PhoneRequest(Contract):
    name: str = Field(default='Mobile number',min_length=1,max_length=80)


class AcceptPhone(Contract):
    price_micros: int = Field(ge=1)


class Wait(Contract):
    inbox_ids: list[str] | None = Field(default=None,max_length=30)
    after: int = Field(ge=0)
    kind: Literal['email','sms'] | None = None
    sender: str = Field(default='',max_length=200)
    has_code: bool = False
    timeout: int = Field(default=25,ge=0,le=120)
    limit: int = Field(default=10,ge=1,le=10)


def code_from(text):
    # Prefer an explicitly labelled code. Never interpret a number from a URL as a code.
    match = re.search(r'(?i)(?:verification|security|sign[ -]?in|login|one[ -]?time)?\s*(?:code|otp|pin)'
                      r'\s*(?:is|:|=)?\s*([0-9](?:[0-9 -]{2,12})[0-9]|[A-Z0-9]{4,10})\b',text)
    if match:
        code = re.sub(r'[ -]','',match[1]).strip()
        if 4<=len(code)<=10 and any(c.isdigit() for c in code):
            return code
    return None


def permit(c, who, *, admin=False, auth=None):
    if who.support or who.role not in ('owner','human','bot'):
        raise Problem('forbidden','Company members and agents only',403)
    validate_identity(c,who)
    if admin and (who.role not in ('owner','human') or auth.company_role(who) not in ('owner','admin')):
        raise Problem('forbidden','An owner or admin manages phone rentals and releases inboxes',403)


def save_inbox(c, item):
    c.execute('INSERT INTO inbound_inboxes(inbox_id,kind,name,address,state,created_by,period_end,cancel_pending) '
              'VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(inbox_id) DO UPDATE SET name=excluded.name,address=excluded.address,state=excluded.state,'
              'period_end=excluded.period_end,cancel_pending=excluded.cancel_pending',
              tuple(item.get(k) for k in ('inbox_id','kind','name','address','state','created_by','period_end','cancel_pending')))


class Inbound:
    def __init__(self, store, settings, auth):
        self.store,self.settings,self.auth = store,settings,auth
        self.key = os.environ.get('TICO_INBOUND_KEY','').strip()
        from .support import hq_url
        self.url = (os.environ.get('TICO_INBOUND_URL','').strip() or hq_url()).rstrip('/')
        self.off = settings.demo or settings.rehearsal or not self.key
        parsed = urlparse(self.url)
        if parsed.scheme!='https' and parsed.hostname not in ('127.0.0.1','localhost','::1'):
            self.off = True
        self.sync_lock = __import__('threading').Lock()
        self.purged_at = 0.0

    def remote(self, method, path, body=None):
        if self.off:
            raise Problem('inbound_unconfigured','Email & Phone is not connected. Ask your owner to set it up.',409)
        try:
            with httpx.Client(transport=TRANSPORT,timeout=35,follow_redirects=False) as client:
                response = client.request(method,self.url+'/v1/inbound/'+path,json=body,
                                          headers={"Authorization":"Bearer "+self.key,"User-Agent": user_agent()})
                if not response.is_success:
                    error = response.json().get('error','inbound_unavailable')
                    allowed = {'email_unavailable','email_quota','creator_rate_limit','sms_unavailable','sms_purchases_disabled',
                               'price_exceeds_cap','monthly_cap','sms_balance_low','phone_quota','operation_conflict',
                               'phone_approval_required','not_found','request_already_provisioned',
                               'quote_expired_or_changed','phone_request_quota'}
                    if error not in allowed:
                        error = 'inbound_unavailable'
                    detail = {'quote_expired_or_changed':'The quote expired or changed. Refresh the phone request before accepting.',
                              'request_already_provisioned':'This request has already entered provisioning. Manage the number from its inbox.',
                              'phone_request_quota':'There are already five open phone requests. Cancel a request before adding another.'}.get(error)
                    raise Problem(error,detail or error.replace('_',' ').capitalize(),response.status_code)
                if len(response.content)>1024*1024:
                    raise ValueError('oversized_delivery')
                return response.json()
        except (httpx.HTTPError,ValueError,KeyError):
            raise Problem('inbound_unavailable','Email & Phone did not answer. Please retry.',503) from None

    def list_inboxes(self, who, after="", limit=100):
        with self.store.read_transaction() as c:
            permit(c,who)
            rows = [dict(r) for r in c.execute('SELECT * FROM inbound_inboxes WHERE inbox_id>? ORDER BY inbox_id LIMIT ?', (after,limit+1))]
            sync = dict(c.execute('SELECT * FROM inbound_sync WHERE singleton=1').fetchone())
        return {'inboxes':rows[:limit],'next':rows[limit-1]['inbox_id'] if len(rows)>limit else None,'configured':not self.off,'last_sync':sync['last_sync'],'error':sync['error'],
                'gaps':json.loads(sync['gaps_json'])}

    def create(self, who, body, operation):
        with self.store.read_transaction() as c:
            permit(c,who,admin=body.kind=='sms',auth=self.auth)
        if body.kind=='sms':
            raise Problem('phone_approval_required','Request a monthly phone rental for TICO approval',409)
        if not operation or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,100}',operation):
            raise Problem('idempotency','Supply an Idempotency-Key of 8 to 100 characters',422)
        request = {'name':body.name,'created_by':who.actor,'operation_id':operation}
        result = self.remote('POST','inboxes/'+body.kind,request)
        def commit(c):
            permit(c,who,admin=body.kind=='sms',auth=self.auth)
            save_inbox(c,result)
            H.event(c,who.actor,'inbound.inbox_created',result['inbox_id'])
            return result
        return self.store.write(who,commit)

    def edit(self, who, ident, body):
        with self.store.read_transaction() as c:
            permit(c,who)
            row = c.execute('SELECT kind,name FROM inbound_inboxes WHERE inbox_id=?',(ident,)).fetchone()
            if not row:
                raise Problem('unknown_inbox','Unknown inbox',404)
            permit(c,who,admin=row['kind']=='sms',auth=self.auth)
            rename = row['name']!=body.name
        result = self.remote('POST','inboxes/'+quote(ident,safe='')+'/rename',{'name':body.name,'created_by':who.actor}) if rename else None
        def commit(c):
            permit(c,who)
            row = c.execute('SELECT kind FROM inbound_inboxes WHERE inbox_id=?',(ident,)).fetchone()
            if not row:
                raise Problem('unknown_inbox','Unknown inbox',404)
            permit(c,who,admin=row['kind']=='sms',auth=self.auth)
            if result is not None:save_inbox(c,result)
            if body.used_for is not None:
                c.execute('UPDATE inbound_inboxes SET used_for=? WHERE inbox_id=?',(body.used_for,ident))
            H.event(c,who.actor,'inbound.inbox_updated',ident)
            return dict(c.execute('SELECT * FROM inbound_inboxes WHERE inbox_id=?',(ident,)).fetchone())
        return self.store.write(who,commit)

    def phone_requests(self, who, after='', limit=100):
        with self.store.read_transaction() as c:
            permit(c,who,admin=True,auth=self.auth)
        return self.remote('GET','phone-requests?'+urlencode({'after':after,'limit':limit}))

    def phone_request(self, who, *, name=None, operation=None, ident=None, action=None, price=None):
        with self.store.read_transaction() as c:
            permit(c,who,admin=True,auth=self.auth)
        body = {'created_by':who.actor}
        if ident is None:
            if not operation or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,100}',operation):
                raise Problem('idempotency','Supply an Idempotency-Key of 8 to 100 characters',422)
            path = 'phone-requests'
            body.update(name=name,operation_id=operation)
        else:
            path = 'phone-requests/'+quote(ident,safe='')+'/'+action
            if action=='accept':body['price_micros'] = price
        result = self.remote('POST',path,body)
        def commit(c):
            permit(c,who,admin=True,auth=self.auth)
            H.event(c,who.actor,'inbound.phone_request_'+(action or 'created'),result['phone_request']['request_id'])
            return result
        return self.store.write(who,commit)

    def release(self, who, ident, confirmation):
        with self.store.read_transaction() as c:
            permit(c,who,admin=True,auth=self.auth)
            row = c.execute('SELECT * FROM inbound_inboxes WHERE inbox_id=?',(ident,)).fetchone()
            if not row:
                raise Problem('unknown_inbox','Unknown inbox',404)
            if row['used_for'] and confirmation!=row['address']:
                raise Problem('confirm_dependencies','Type the address or number to confirm its saved dependencies',409)
        result = self.remote('POST','inboxes/'+ident+'/release',{})
        def commit(c):
            permit(c,who,admin=True,auth=self.auth)
            save_inbox(c,result)
            H.event(c,who.actor,'inbound.inbox_released',ident)
            return result
        return self.store.write(who,commit)

    def scope(self,c,ids):
        if ids is None:
            return []
        if not ids or len(ids)>30 or len(set(ids))!=len(ids):
            raise Problem('unknown_inbox','Choose one or more distinct inboxes',422)
        for ident in ids:
            if not c.execute('SELECT 1 FROM inbound_inboxes WHERE inbox_id=?',(ident,)).fetchone():
                raise Problem('unknown_inbox','Unknown inbox',404)
        return ids

    def listing(self,who,*,inbox_ids=None,kind=None,sender='',q='',has_code=False,after=0,before=0,limit=50,ascending=False,spam=False):
        cutoff = now()-RETENTION
        with self.store.read_transaction() as c:
            permit(c,who)
            ids = self.scope(c,inbox_ids)
            clauses,values = ['seq>?'],[after]
            # Content past the cutoff is treated as removed even before the next purge.
            for condition,value in [('seq<?',before),('kind=?',kind),('sender LIKE ?',('%'+sender+'%') if sender else None),
                                    ('((body LIKE ? AND received_at>=?) OR subject LIKE ?)',('%'+q+'%') if q else None)]:
                if value:
                    clauses.append(condition);values.append(value)
                    if condition.startswith('((body'):
                        values.extend((cutoff,value))
            if ids:
                clauses.append('inbox_id IN ('+','.join('?' for _ in ids)+')');values.extend(ids)
            if has_code:clauses.append('code IS NOT NULL AND received_at>=?');values.append(cutoff)
            if not spam:clauses.append('spam=0')
            order = 'ASC' if ascending else 'DESC'
            rows = c.execute('SELECT seq,message_id,inbox_id,kind,received_at,sender,subject,'
                             "CASE WHEN received_at<? THEN '' ELSE preview END AS preview,"
                             'code IS NOT NULL AND received_at>=? AS has_code,spam,truncated,'
                             '(retention_removed OR received_at<?) AS retention_removed FROM inbound_messages WHERE '+
                             ' AND '.join(clauses)+' ORDER BY seq '+order+' LIMIT ?',
                             [cutoff]*3+values+[limit+1]).fetchall()
            current = c.execute('SELECT coalesce(max(seq),0) FROM inbound_messages').fetchone()[0]
        result,used = [],0
        for row in rows[:limit]:
            item = dict(row);item['from'] = item.pop('sender')
            size = len(encode(item).encode())
            if used+size>96*1024:break
            result.append(item);used+=size
        more = len(rows)>len(result)
        # With ascending waits only advance past rows actually returned. On timeout, all nonmatching rows were scanned.
        cursor = result[-1]['seq'] if ascending and more and result else current
        return {'messages':result,'next':result[-1]['seq'] if more and result else None,'cursor':cursor}

    def read(self,who,ident,offset=0,max_chars=32768,code=False):
        def run(c):
            permit(c,who)
            row = c.execute('SELECT * FROM inbound_messages WHERE message_id=?',(ident,)).fetchone()
            if not row:raise Problem('not_found','Message not found',404)
            if row['retention_removed'] or row['received_at']<now()-RETENTION:
                self.expire(c)
                raise Problem('retention','Message content was removed by retention',410)
            if code:return {'code':row['code'],'untrusted':True}
            body = row['body'] or ''
            # max_chars also observes a byte cap for multibyte text.
            text = body[offset:offset+max_chars].encode()[:32768].decode(errors='ignore')
            end = offset+len(text)
            return {'message_id':ident,'inbox_id':row['inbox_id'],'text':text,'untrusted':True,
                    'next_offset':end if end<len(body) else None,'truncated':bool(row['truncated'])}
        def audited(c):
            try:
                result = run(c)
            except Problem as problem:
                if problem.code!='retention':raise
                return problem  # commit the purge, then refuse
            H.event(c,who.actor,'inbound.read',ident)
            return result
        result = self.store.write(who,audited)
        if isinstance(result,Problem):raise result
        return result

    def expire(self,c=None):
        """Remove bodies, codes and previews past retention. Local only: never waits on HQ or a key."""
        if c is None:
            with self.store.transaction() as c:
                return self.expire(c)
        self.purged_at = now()
        return c.execute("UPDATE inbound_messages SET body=NULL,code=NULL,preview='',retention_removed=1 WHERE "
                         'retention_removed=0 AND received_at<?',(self.purged_at-RETENTION,)).rowcount

    def ingest(self,page):
        deliveries = page.get('deliveries',[])
        if len(deliveries)>50 or len(encode(page).encode())>1024*1024:
            raise ValueError('invalid_delivery_page')
        with self.store.transaction() as c:
            cursor = c.execute('SELECT cursor FROM inbound_sync WHERE singleton=1').fetchone()[0]
            through = page['cursor']
            if not isinstance(through,int) or through<cursor:
                raise ValueError('invalid_cursor')
            for item in deliveries:
                if not cursor<item['hq_seq']<=through:
                    raise ValueError('invalid_delivery_order')
                if not c.execute('SELECT 1 FROM inbound_inboxes WHERE inbox_id=?',(item['inbox_id'],)).fetchone():
                    raise ValueError('unknown_delivery_inbox')
                text = str(item.get('text') or '')[:200000]
                code = str(item['code'])[:32] if item.get('code') else code_from(text)
                inserted = c.execute('INSERT OR IGNORE INTO inbound_messages(message_id,inbox_id,kind,received_at,sender,subject,'
                            'preview,body,code,spam,truncated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (item['delivery_id'],item['inbox_id'],item['kind'],item['received_at'],str(item.get('from',''))[:300],
                             str(item.get('subject',''))[:300].replace(code,'••••••') if code else str(item.get('subject',''))[:300],
                             text[:300].replace(code,'••••••') if code else text[:300],text,code,bool(item.get('spam')),bool(item.get('truncated'))))
                if inserted.rowcount:
                    from .routines import emit
                    emit(c,'inbound.received',item['delivery_id'],{'message_id':item['delivery_id'],'inbox_id':item['inbox_id'],
                                                                'kind':item['kind'],'has_code':bool(code)})
            # Keep cumulative gap summaries; the cursor commit and gap update are atomic with messages.
            old = json.loads(c.execute('SELECT gaps_json FROM inbound_sync WHERE singleton=1').fetchone()[0])
            gaps = {(g['inbox_id'],g['from'],g['to']):g for g in old+page.get('gaps',[])}
            c.execute("UPDATE inbound_sync SET cursor=?,last_sync=?,error='',gaps_json=? WHERE singleton=1",
                      (through,time.time(),encode(list(gaps.values())[-100:])))
            self.expire(c)

    def sync(self):
        if self.off or not self.sync_lock.acquire(blocking=False):return
        try:
            after = ''
            for _ in range(100):
                result = self.remote('GET','inboxes'+('?after='+after if after else ''))
                with self.store.transaction() as c:
                    for item in result['inboxes']:save_inbox(c,item)
                after = result.get('next')
                if not after:break
            else:
                raise ValueError('inbox_sync_backlog')
            with self.store.read() as c:
                cursor = c.execute('SELECT cursor FROM inbound_sync WHERE singleton=1').fetchone()[0]
            page = self.remote('POST','pull',{'cursor':cursor,'wait':0})
            self.ingest(page)
            # A previous lost ack is retried even when there was no new delivery on this pull.
            self.remote('POST','ack',{'through_hq_seq':page['cursor']})
        except Exception:
            with self.store.transaction() as c:
                c.execute("UPDATE inbound_sync SET error='inbound_unavailable' WHERE singleton=1")
            raise
        finally:
            self.sync_lock.release()

    def tick(self):
        # Retention runs first and also with no key, in demo or during an HQ outage.
        if now()-self.purged_at>=PURGE_EVERY:
            self.expire()
        self.sync()

    async def loop(self,stop):
        while not stop.is_set():
            try:await asyncio.to_thread(self.tick)
            except Exception:pass  # stable error code in Settings, never message content or a supplier traceback
            try:await asyncio.wait_for(stop.wait(),15)
            except TimeoutError:pass


def install(app,store,settings,auth):
    service = Inbound(store,settings,auth)
    app.state.inbound = service

    @app.get('/api/v2/inbound/inboxes')
    def inboxes(request:Request,after:str=Query(default='',max_length=64),limit:int=Query(default=100,ge=1,le=100)):
        return service.list_inboxes(request.state.identity,after,limit)

    @app.post('/api/v2/inbound/inboxes')
    def create(request:Request,body:Create):return service.create(request.state.identity,body,request.headers.get('Idempotency-Key'))

    @app.patch('/api/v2/inbound/inboxes/{ident}')
    def edit(request:Request,ident:str,body:Edit):return service.edit(request.state.identity,ident,body)

    @app.post('/api/v2/inbound/inboxes/{ident}/release')
    def release(request:Request,ident:str,body:Release):return service.release(request.state.identity,ident,body.confirm)

    @app.get('/api/v2/inbound/phone-quote')
    def quote(request:Request):
        with store.read_transaction() as c:permit(c,request.state.identity,admin=True,auth=auth)
        return service.remote('GET','phone-quote')

    @app.get('/api/v2/inbound/phone-requests')
    def phone_requests(request:Request,after:str=Query(default='',max_length=64),limit:int=Query(default=100,ge=1,le=100)):
        return service.phone_requests(request.state.identity,after,limit)

    @app.post('/api/v2/inbound/phone-requests')
    def request_phone(request:Request,body:PhoneRequest):
        return service.phone_request(request.state.identity,name=body.name,operation=request.headers.get('Idempotency-Key'))

    @app.post('/api/v2/inbound/phone-requests/{ident}/accept')
    def accept_phone(request:Request,ident:str,body:AcceptPhone):
        return service.phone_request(request.state.identity,ident=ident,action='accept',price=body.price_micros)

    @app.post('/api/v2/inbound/phone-requests/{ident}/cancel')
    def cancel_phone(request:Request,ident:str):
        return service.phone_request(request.state.identity,ident=ident,action='cancel')

    @app.get('/api/v2/inbound/cursor')
    def cursor(request:Request,inbox_ids:str|None=Query(default=None,max_length=1000)):
        return {'cursor':service.listing(request.state.identity,inbox_ids=inbox_ids.split(',') if inbox_ids is not None else None,limit=1)['cursor']}

    @app.get('/api/v2/inbound/messages')
    def messages(request:Request,inbox_ids:str|None=Query(default=None,max_length=1000),kind:Literal['email','sms']|None=None,
                 sender:str=Query(default='',max_length=200,alias='from'),q:str=Query(default='',max_length=200),has_code:bool=False,
                 after:int=Query(default=0,ge=0),before:int=Query(default=0,ge=0),limit:int=Query(default=50,ge=1,le=100),spam:bool=False):
        return service.listing(request.state.identity,inbox_ids=inbox_ids.split(',') if inbox_ids is not None else None,
                               kind=kind,sender=sender,q=q,has_code=has_code,after=after,before=before,limit=limit,spam=spam)

    @app.get('/api/v2/inbound/messages/{ident}')
    def read(request:Request,ident:str,offset:int=Query(default=0,ge=0),max_chars:int=Query(default=32768,ge=1,le=32768)):
        return service.read(request.state.identity,ident,offset,max_chars)

    @app.get('/api/v2/inbound/messages/{ident}/code')
    def code(request:Request,ident:str):return service.read(request.state.identity,ident,code=True)

    @app.post('/api/v2/inbound/wait')
    async def wait(request:Request,body:Wait):
        until = asyncio.get_running_loop().time()+body.timeout
        while True:
            result = await asyncio.to_thread(service.listing,request.state.identity,inbox_ids=body.inbox_ids,after=body.after,
                                            kind=body.kind,sender=body.sender,has_code=body.has_code,limit=body.limit,ascending=True)
            if result['messages'] or asyncio.get_running_loop().time()>=until:return result
            await asyncio.sleep(.5)
    return service
