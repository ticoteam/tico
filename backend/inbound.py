"""Company inbound inboxes. Shared by people and agents; reads never consume messages.

HQ owns supplier credentials. This install holds its own inbound key and pulls normalized text only.
"""
import asyncio
import json
import math
import os
import re
import time
import uuid
from dataclasses import replace
from typing import Literal
from urllib.parse import quote, urlencode, urlparse

import httpx
from fastapi import Query, Request
from pydantic import ConfigDict, Field, ValidationError

from clients.gateway import user_agent

from .auth import validate_identity
from . import outbound_tls
from .models import Contract
from .store import H, Problem, encode

TRANSPORT = None  # tests' fake HQ, never a live company database
RETENTION = 90*86400  # bodies and codes; metadata stays
PURGE_EVERY = 600  # reads refuse expired content before the next purge
CONNECTION_TIMEOUT = 15  # includes headers and a slow-drip response, not just each socket read
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
 last_synced_at REAL, error TEXT NOT NULL DEFAULT '', gaps TEXT NOT NULL DEFAULT '[]');
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


class CompanyConnection(Contract):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=False)
    action: Literal['preview','connect']
    code: str = Field(strict=True,min_length=43,max_length=43,pattern=r'^[A-Za-z0-9_-]{43}$')


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


def connection_json(raw):
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate_field')
            result[key] = value
        return result
    return json.loads(raw,object_pairs_hook=unique)


class Inbound:
    def __init__(self, store, settings, auth):
        self.store,self.settings,self.auth = store,settings,auth
        self.judge = lambda: None                  # the app's decision model, set by install
        self.wake_mail = lambda: None              # the physical-mail drain's kick, set by install
        self.blobs = self.files = lambda: None     # the blob store and Library, for the bills address; set by install
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
            with httpx.Client(transport=TRANSPORT,timeout=35,follow_redirects=False,verify=outbound_tls.context()) as client:
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
        return {'inboxes':rows[:limit],'next':rows[limit-1]['inbox_id'] if len(rows)>limit else None,'configured':not self.off,'last_synced_at':sync['last_synced_at'],'error':sync['error'],
                'gaps':json.loads(sync['gaps'])}

    def connection_actor(self, who, what='Inbox', code='inbox_connection'):
        # Delegated human identities may manage other Inbox operations, never company linking. Also the rule for
        # linking this server to its company (backend/company_server.py), with its own `what` and error `code`.
        forbidden = Problem(code+'_forbidden',f'A signed-in owner or admin must connect {what} directly.',403)
        if (who.role not in ('owner','human') or not who.actor.startswith('human:')
                or any((who.support,who.via,who.via_token,who.runner_id,who.attempt_id,who.agent,who.task_actor))):
            raise forbidden
        from . import access, census
        with self.store.read_transaction() as c:
            validate_identity(c,who)
            human = H.human(c,H.actor_id(who.actor))
            role = access.role_of(access.load_access(c,self.settings),access.load_owner(c,self.settings)['email'],human.get('email'))
            if role not in ('owner','admin'):
                raise forbidden
            validate_identity(c,replace(who,role='owner' if role=='owner' else 'human',email=human.get('email') or ''))
            # This getter must stay read-only: a reported census ID does not prove ownership.
            try:record = census._load(c)
            except (ValueError,TypeError):record = {}
        ident = record.get('install_id')
        try:
            if not isinstance(ident,str) or str(uuid.UUID(ident))!=ident:raise ValueError()
        except (ValueError,TypeError,AttributeError):
            raise Problem(code+'_unavailable','This workspace has no saved installation identity yet.',409) from None
        if len(who.actor)>100 or any(ord(ch)<32 or ord(ch)==127 for ch in who.actor):
            raise forbidden
        return {'reported_install_id':ident,'actor':who.actor}

    async def company_connection(self, who, body):
        proof = self.connection_actor(who)
        unavailable = 'Inbox could not confirm the connection. Retry the same code or check the portal.'
        if self.off or self.url not in ('https://api.tico.dev','https://updates.tico.team'):
            raise Problem('inbox_connection_unavailable',unavailable,503,retryable=True)
        errors = {
            'inbox_connection_invalid':(422,'Enter the connection code from the portal.'),
            'inbox_connection_expired':(409,'This connection code expired. Get a new code in the portal.'),
            'inbox_connection_conflict':(409,'This Inbox cannot connect to that company. Check its existing connection in the portal.'),
            'inbox_connection_forbidden':(403,'The connection is no longer authorized. Check your company access in the portal.'),
        }
        try:
            # Identity encoding plus raw streaming bounds both wire and decoded JSON bytes.
            async with asyncio.timeout(CONNECTION_TIMEOUT):
                async with httpx.AsyncClient(transport=TRANSPORT,timeout=10,trust_env=False,follow_redirects=False) as client:
                    async with client.stream('POST',self.url+'/v1/inbound/company-connection',
                                       json={**body.model_dump(),**proof},headers={'Authorization':'Bearer '+self.key,
                                       'User-Agent':user_agent(),'Accept-Encoding':'identity'}) as response:
                        if 300<=response.status_code<400:raise ValueError()
                        if response.headers.get('content-encoding','identity').lower() not in ('','identity'):
                            raise ValueError()
                        chunks,total = [],0
                        async for chunk in response.aiter_raw(chunk_size=1024):
                            total += len(chunk)
                            if total>4096:raise ValueError()
                            chunks.append(chunk)
                        result = connection_json(b''.join(chunks))
                        if response.status_code!=200:
                            code = result.get('error') if isinstance(result,dict) else None
                            if isinstance(code,str) and code in errors and response.status_code==errors[code][0]:
                                raise Problem(code,errors[code][1],response.status_code)
                            raise ValueError()
            expected = {'connected','scope','company'} | ({'expires_at'} if body.action=='preview' else set())
            if (not isinstance(result,dict) or set(result)!=expected or result['scope']!='inbox'
                    or result['connected'] is not (body.action=='connect')):raise ValueError()
            company = result['company']
            if (not isinstance(company,dict) or set(company)!={'company_id','name'}
                    or not isinstance(company['company_id'],str)
                    or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}',company['company_id'])
                    or not isinstance(company['name'],str) or not 1<=len(company['name'])<=100
                    or any(ord(ch)<32 or ord(ch)==127 for ch in company['name'])):raise ValueError()
            company['name'].encode('utf-8')
            if body.action=='preview' and (type(result['expires_at']) not in (int,float)
                    or not math.isfinite(result['expires_at']) or result['expires_at']<=0):raise ValueError()
            return result
        except (httpx.HTTPError,httpx.StreamError,TimeoutError,ValueError,KeyError,TypeError,OverflowError,RecursionError):
            raise Problem('inbox_connection_unavailable',unavailable,503,retryable=True) from None

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

    def screen(self,who,ident):
        """The screening verdict on a message a bot reads (backend/screening.py), outside the write: it may wait on the
        decision model. Kept per message, so another read or part costs nothing. None when there is nothing to read."""
        from . import screening
        with self.store.read() as c:
            permit(c,who)
            row = c.execute('SELECT subject,body,retention_removed FROM inbound_messages WHERE message_id=?',(ident,)).fetchone()
        if not row or row['retention_removed'] or not row['body']:return None
        # The subject too: an injection can start in the subject and go on in the body.
        text = f"{row['subject']}\n\n{row['body']}" if row['subject'] else row['body']
        found = screening.screen(self.store,'mail',text,H.actor_id(who.actor),key=ident,primary=self.judge())
        return {**found,'action':'warn'} if found['action']=='hold' else found   # mail is marked, never held

    def read(self,who,ident,offset=0,max_chars=32768,code=False):
        # Mail is never hidden: a bot's read carries the verdict, and at warn or hold the text starts with the warning.
        screened = None if code or who.role!='bot' else self.screen(who,ident)
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
            result = {'message_id':ident,'inbox_id':row['inbox_id'],'text':text,'untrusted':True,
                      'next_offset':end if end<len(body) else None,'truncated':bool(row['truncated'])}
            if screened is not None:
                from . import screening
                result['screening'] = screening.public(screened)
                if offset==0 and screened['action']!='pass':
                    result['text'] = screening.warning_line(screened)+'\n\n'+text
                screening.note(c,screened,'mail',ident,once=True,bot=H.actor_id(who.actor))
            return result
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
        from . import bills_address, postal_mail
        kicked = False
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
                    H.hook(c,'inbound.received',item['delivery_id'],lambda: {'message_id':item['delivery_id'],'inbox_id':item['inbox_id'],
                                                                           'kind':item['kind']},'keeper',revision=0)
                    # A scan another mail provider emailed to the mail-intake inbox becomes a letter (backend/postal_mail.py).
                    if postal_mail.emailed(c,item):kicked = True
                    # A bill emailed to the bills address waits for the drain below (backend/bills_address.py).
                    bills_address.emailed(c,item)
            # Keep cumulative gap summaries; the cursor commit and gap update are atomic with messages.
            old = json.loads(c.execute('SELECT gaps FROM inbound_sync WHERE singleton=1').fetchone()[0])
            gaps = {(g['inbox_id'],g['from'],g['to']):g for g in old+page.get('gaps',[])}
            c.execute("UPDATE inbound_sync SET cursor=?,last_synced_at=?,error='',gaps=? WHERE singleton=1",
                      (through,time.time(),encode(list(gaps.values())[-100:])))
            self.expire(c)
        if kicked:self.wake_mail()

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
        # Bills from the bills address: their bytes come from HQ like the messages, then become draft bills.
        from . import bills_address
        bills_address.drain(self,self.store,self.blobs(),self.files())

    async def loop(self,stop):
        while not stop.is_set():
            try:await asyncio.to_thread(self.tick)
            except Exception:pass  # stable error code in Settings, never message content or a supplier traceback
            try:await asyncio.wait_for(stop.wait(),15)
            except TimeoutError:pass


def install(app,store,settings,auth):
    service = Inbound(store,settings,auth)
    service.judge = lambda: getattr(app.state,'judge',None)
    service.wake_mail = lambda: getattr(app.state,'postal_mail',None) and app.state.postal_mail.kick()
    service.blobs = lambda: getattr(app.state,'blobs',None)
    service.files = lambda: getattr(app.state,'files',None)
    app.state.inbound = service

    @app.post('/api/v2/inbound/company-connection')
    async def company_connection(request:Request):
        # Authenticate before parsing; ordinary validation errors can reveal supplied secrets.
        who = request.state.identity
        service.connection_actor(who)
        try:
            if request.headers.get('content-type','').split(';',1)[0]!='application/json':raise ValueError()
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw)+len(chunk)>2048:raise ValueError()
                raw.extend(chunk)
            body = CompanyConnection.model_validate(connection_json(raw))
        except (ValueError,TypeError,ValidationError,RecursionError):
            raise Problem('inbox_connection_invalid','Enter the connection code from the portal.',422) from None
        return await service.company_connection(who,body)

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
