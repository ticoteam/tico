import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from backend.credentials import CredentialCipher
from backend.tests.test_api import api,post,get,runner,assign,ready,claim,headers


class FakeKMS:
    def __init__(self): self.key=os.urandom(32)
    def generate_data_key(self,**kwargs):
        assert kwargs['EncryptionContext']=={'application':'tico-credentials'}
        key=os.urandom(32);nonce=os.urandom(12)
        return {'Plaintext':key,'CiphertextBlob':nonce+AESGCM(self.key).encrypt(nonce,key,b'kms')}
    def decrypt(self,**kwargs):
        blob=kwargs['CiphertextBlob']
        return {'Plaintext':AESGCM(self.key).decrypt(blob[:12],blob[12:],b'kms')}


def setup(api):
    api.app.state.store.settings.credential_kms_key='test-key'
    api.app.state.store.settings.credential_admins=('ana@acme.example','ben@acme.example')
    api.app.state.vault.cipher=CredentialCipher('test-key',FakeKMS())


def create(api,**kwargs):
    return post(api,'credentials',{'name':'PostHog','secret':'phx-synthetic-private-987654','env':'POSTHOG_API_KEY',**kwargs})


def test_ciphertext_only_and_authorizers_and_validation_redaction(api):
    setup(api)
    row=create(api)
    assert row['preview']=='phx…654' and row['stored']
    assert 'synthetic-private' not in json.dumps(get(api,'credentials'))
    assert get(api,'credentials','ben-test')['can_manage']
    post(api,'credentials',{'name':'No','secret':'secret'},'cara-test',expected=403)
    get(api,'credentials','cara-test',expected=403)
    secret='  space-sensitive-password  '
    pw=create(api,name='Password',kind='password',username='person@acme.example',secret=secret)
    assert pw['preview']=='••••••'
    assert post(api,f"credentials/{pw['id']}/reveal",{})['value']==secret
    with api.app.state.store.read() as c:
        for table in ('credentials','idempotency','events'):
            dump=str([tuple(r) for r in c.execute(f'SELECT * FROM {table}')])
            assert secret not in dump and 'phx-synthetic-private-987654' not in dump
        data=c.execute('SELECT ciphertext,nonce FROM credentials WHERE id=?',(row['id'],)).fetchone()
        assert isinstance(data['ciphertext'],bytes) and len(data['nonce'])==12
    bad=post(api,'credentials',{'name':'Bad','secret':'do-not-echo','unexpected':'do-not-echo'},expected=422)
    assert 'do-not-echo' not in json.dumps(bad)


def test_explicit_human_grants_delegate_only_to_owned_bots_and_revoke_cascades(api):
    setup(api);row=create(api);cid=row['id']
    # Ana and Ben authorize; unrelated registered people start with no access.
    parent=post(api,f'credentials/{cid}/grants',{'subject':'human:cara'},'ben-test')
    assert len(get(api,'credentials','cara-test')['credentials'])==1
    assert post(api,f'credentials/{cid}/reveal',{},'cara-test')['value'].startswith('phx-')
    post(api,f'credentials/{cid}/grants',{'subject':'human:ben'},'cara-test',expected=403)
    post(api,f'credentials/{cid}/grants',{'subject':'bot:ops'},'cara-test',expected=403)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara' WHERE bot='finance'")
    child=post(api,f'credentials/{cid}/grants',{'subject':'bot:finance'},'cara-test')
    machine=runner(api);assign(api,machine,'finance');ready(api,machine,['finance'])
    post(api,'chat/finance',{'text':'Use only the synthetic fixture'})
    attempt=claim(api,machine)
    assert attempt['credential_vault'] is True
    assert get(api,'credential-runtime',attempt['token'])['credentials'][0]['value'].startswith('phx-')
    get(api,'credential-runtime',machine['token'],expected=403)
    post(api,f'credentials/{cid}/grants/{parent["id"]}/revoke',{},'ben-test')
    assert get(api,'credential-runtime',attempt['token'])['credentials']==[]
    post(api,f'credentials/{cid}/reveal',{},'cara-test',expected=403)
    # Regranting a person does not resurrect their old delegated grants.
    post(api,f'credentials/{cid}/grants',{'subject':'human:cara'})
    assert get(api,'credential-runtime',attempt['token'])['credentials']==[]


def member_stores(api,bot,env,value,token='cara-test',expected=200):
    return post(api,'credential-set',{'env':env,'for_bot':bot,'value':value,'name':env.title()},token,expected=expected)


def test_the_person_who_stored_a_credential_shares_it_only_with_their_own_bots(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara' WHERE bot IN ('finance','cpo')")
    mine=member_stores(api,'cpo','POSTHOG_API_KEY','phx-synthetic-private-987654')['id']
    theirs=create(api,name='Other',env='OTHER_KEY')['id']
    listing=get(api,'credentials','cara-test')
    assert [r['id'] for r in listing['credentials']]==[mine] and not listing['credentials'][0]['can_reveal']
    assert 'finance' in [b['id'] for b in listing['bots']] and 'ops' not in [b['id'] for b in listing['bots']]
    post(api,f'credentials/{mine}/reveal',{},'cara-test',expected=403)
    given=post(api,f'credentials/{mine}/grants',{'subject':'bot:finance'},'cara-test')
    refused=post(api,f'credentials/{mine}/grants',{'subject':'bot:ops'},'cara-test',expected=403)
    assert 'bots you own or manage' in refused['error']['detail'] and 'Ask' in refused['error']['detail']
    post(api,f'credentials/{mine}/grants',{'subject':'human:ben'},'cara-test',expected=403)
    post(api,f'credentials/{theirs}/grants',{'subject':'bot:finance'},'cara-test',expected=403)
    ops=post(api,f'credentials/{mine}/grants',{'subject':'bot:ops'})
    post(api,f'credentials/{mine}/grants/{ops["id"]}/revoke',{},'cara-test',expected=403)
    machine=runner(api);assign(api,machine,'finance');ready(api,machine,['finance'])
    post(api,'chat/finance',{'text':'Use only the synthetic fixture'})
    attempt=claim(api,machine)
    assert [r['id'] for r in get(api,'credential-runtime',attempt['token'])['credentials']]==[mine]
    post(api,f'credentials/{mine}/grants/{given["id"]}/revoke',{},'cara-test')
    assert get(api,'credential-runtime',attempt['token'])['credentials']==[]


def test_a_company_credential_is_never_its_adders_and_a_members_own_ends_when_rotated_or_the_rule_is_off(api):
    setup(api)
    settings,store=api.app.state.store.settings,api.app.state.store
    with store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara' WHERE bot IN ('finance','cpo')")
    # Cara adds company credentials while she is an Admin, by hand and through a card for a bot she runs, then is demoted.
    settings.credential_admins=('ana@acme.example','ben@acme.example','cara@acme.example')
    company=post(api,'credentials',{'name':'Company GitHub','secret':'ghp-company-secret-123456','env':'GITHUB_TOKEN'},'cara-test')['id']
    carded=member_stores(api,'finance','SENTRY_TOKEN','sentry-company-secret-123')['id']
    settings.credential_admins=('ana@acme.example','ben@acme.example')
    for cid in (company,carded):
        post(api,f'credentials/{cid}/grants',{'subject':'bot:cpo'},'cara-test',expected=403)
    get(api,'credentials','cara-test',expected=403)
    # Storing under the same variable is a new credential of hers; the company's value is never overwritten.
    again=member_stores(api,'cpo','GITHUB_TOKEN','member-own-value-123456')
    assert again['id']!=company and not again['replaced']
    post(api,f'credentials/{company}',{'name':'Company GitHub','env':'GITHUB_TOKEN','secret':'member-own-value-123456',
                                       'expected_revision':1},'cara-test',expected=403)
    assert post(api,f'credentials/{company}/reveal',{})['value']=='ghp-company-secret-123456'
    # A bot an administrator gave the company's value keeps it; the refusal names it and whom to ask.
    post(api,f'credentials/{company}/grants',{'subject':'bot:finance'})
    busy=member_stores(api,'finance','GITHUB_TOKEN','member-other-value-123456',expected=409)
    assert 'from the credential Company GitHub' in busy['error']['detail'] and 'Ask Ana' in busy['error']['detail']
    with store.read() as c:                       # the refusal leaves the grant it named in place
        assert c.execute("SELECT count(*) FROM credential_grants WHERE credential_id=? AND subject='bot:finance' "
                         'AND revoked IS NULL',(company,)).fetchone()[0]==1

    # A server that ran the old backfill: no row already there stays anyone's, even one a member stored.
    with store.transaction() as c:
        c.execute('ALTER TABLE credentials DROP COLUMN member_stored')
    store.initialize()
    with store.read() as c:
        assert c.execute('SELECT count(*) FROM credentials WHERE member_stored=1').fetchone()[0]==0
    post(api,f'credentials/{again["id"]}/grants',{'subject':'bot:finance'},'cara-test',expected=403)

    # Her own credential stops being hers once someone else saves a new value for it.
    mine=member_stores(api,'finance','POSTHOG_API_KEY','phx-member-own-value-987')['id']
    post(api,f'credentials/{mine}/grants',{'subject':'bot:cpo'},'cara-test')
    row=post(api,f'credentials/{mine}',{'name':'Posthog_Api_Key','env':'POSTHOG_API_KEY','secret':'phx-rotated-by-admin-987',
                                        'expected_revision':1},'ben-test')
    post(api,f'credentials/{mine}/grants',{'subject':'bot:finance'},'cara-test',expected=403)
    # Storing it again cannot replace the rotated value, and says what to take away first (her own grant, so no one to ask).
    busy=member_stores(api,'finance','POSTHOG_API_KEY','phx-member-again-value-987',expected=409)
    assert 'Take Posthog_Api_Key away from finance' in busy['error']['detail'] and 'Ask' not in busy['error']['detail']
    with store.read() as c:
        assert c.execute("SELECT count(*) FROM credential_grants WHERE credential_id=? AND subject='bot:finance' "
                         'AND revoked IS NULL',(mine,)).fetchone()[0]==1
    assert post(api,f'credentials/{mine}/reveal',{})['value']=='phx-rotated-by-admin-987' and row['revision']==2

    # The owner turning the rule off ends what is hers too.
    own=member_stores(api,'cpo','SLACK_TOKEN','xoxb-member-own-value-123')['id']
    r=api.put('/api/v2/access/rules',json={'members_store_credentials':False},headers=headers())
    assert r.status_code==200,r.text
    post(api,f'credentials/{own}/grants',{'subject':'bot:finance'},'cara-test',expected=403)
    get(api,'credentials','cara-test',expected=403)


def test_admin_deletes_the_value_and_all_grants_with_metadata_only_history(api):
    setup(api)
    row=create(api)
    parent=post(api,f"credentials/{row['id']}/grants",{'subject':'human:cara'})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET operator='cara' WHERE bot='finance'")
    post(api,f"credentials/{row['id']}/grants",{'subject':'bot:finance'},'cara-test')
    assert api.delete('/api/v2/credentials/'+row['id'],headers=headers('cara-test')).status_code==403
    token=post(api,'me/tokens',{'label':'QA cleanup'},'ben-test')['token']
    from backend.tests.test_mcp import call
    err,out=call(api,'hub_credential_delete',{'credential':row['name']},token=token)
    assert not err and out['deleted']
    with api.app.state.store.read() as c:
        assert not c.execute('SELECT 1 FROM credentials WHERE id=?',(row['id'],)).fetchone()
        assert not c.execute('SELECT 1 FROM credential_grants WHERE credential_id=?',(row['id'],)).fetchone()
        event=c.execute("SELECT detail_json FROM events WHERE action='credential.deleted' AND target=?",(row['id'],)).fetchone()[0]
        assert 'PostHog' in event and 'synthetic-private' not in event and 'ciphertext' not in event


def test_upgrade_migration_is_assigned_once_and_never_resurrects_revoked_grants(api):
    setup(api)
    from backend.credentials import FILE_MIGRATION
    machine=runner(api);assign(api,machine,'ops');ready(api,machine,['ops'])
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE registry_metadata SET value_json=? WHERE key=?',(json.dumps(['ops']),FILE_MIGRATION))
    assert get(api,'runner-credential-migration',machine['token'])['bots']==['ops']
    values={'bot':'ops','credentials':[{'env':'QA_MIGRATED_KEY','value':'fixture-private-migration'}]}
    post(api,'runner-credential-migration',values,'cara-test',expected=403)
    other=runner(api,label='Other Computer')
    post(api,'runner-credential-migration',values,other['token'],expected=403)
    post(api,'runner-credential-migration',values,machine['token'])
    assert get(api,'runner-credential-migration',machine['token'])['bots']==[]
    assert get(api,'runner-credential-grants',machine['token'])['bots']=={'ops':['QA_MIGRATED_KEY']}
    post(api,'chat/ops',{'text':'Check granted fixture'})
    attempt=claim(api,machine,'ops')
    delivered=get(api,'credential-runtime',attempt['token'])['credentials']
    assert delivered[0]['env']=='QA_MIGRATED_KEY' and delivered[0]['value']=='fixture-private-migration'
    row=get(api,'credentials')['credentials'][0]
    post(api,f"credentials/{row['id']}/grants/{row['grants'][0]['id']}/revoke",{})
    post(api,'runner-credential-migration',values,machine['token'])
    assert get(api,'credential-runtime',attempt['token'])['credentials']==[]
    # A retry across an upgrade cannot overwrite an explicitly revoked variable either.
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE registry_metadata SET value_json=? WHERE key=?',(json.dumps(['ops']),FILE_MIGRATION))
    post(api,'runner-credential-migration',values,machine['token'])
    assert get(api,'runner-credential-grants',machine['token'])['bots']=={'ops':[]}
    # The first pass on an unreported runner preserves a later HUB_ pass for the upgraded runner.
    from backend.credentials import HUB_MIGRATION
    with api.app.state.store.read() as c:
        assert 'ops' in json.loads(c.execute('SELECT value_json FROM registry_metadata WHERE key=?',(HUB_MIGRATION,)).fetchone()[0])
    post(api,'runners/heartbeat',{'version':'test','platform':'test','release':'0.2.32'},machine['token'])
    assert get(api,'runner-credential-migration',machine['token'])['bots']==['ops']
    post(api,'runner-credential-migration',{'bot':'ops','credentials':[]},machine['token'])
    assert get(api,'runner-credential-migration',machine['token'])['bots']==[]



def test_a_reserved_variable_name_is_refused_and_an_old_one_is_named_in_health(api):
    setup(api)
    refused=post(api,'credentials',{'name':'Acme update key','secret':'k-synthetic-123456','env':'TICO_UPDATE_KEY_ACME'},expected=422)
    assert refused['error']['code']=='env' and 'TICO_UPDATE_KEY_ACME' in refused['error']['detail']
    row=create(api,name='Acme update key',env='UPDATE_KEY_ACME')
    # A credential stored before the rule keeps its name, cannot be given to another bot, and Health names it.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE credentials SET env='TICO_UPDATE_KEY_ACME' WHERE id=?",(row['id'],))
        c.execute("INSERT INTO credential_grants(id,credential_id,subject,granted_by,created) VALUES('g-old',?,'bot:ops','human:ana','2026-01-01')",
                  (row['id'],))
    assert post(api,f"credentials/{row['id']}/grants",{'subject':'bot:cpo'},expected=422)['error']['code']=='env'
    rotate={'name':'Acme update key','secret':'k-synthetic-rotated','expected_revision':row['revision']}
    kept=post(api,f"credentials/{row['id']}",{**rotate,'env':'TICO_UPDATE_KEY_ACME'},expected=422)
    assert 'Rename the variable in the same save' in kept['error']['detail']
    check={x['id']:x for x in get(api,'health')['checks']}['reserved_credentials']
    assert 'Acme update key (TICO_UPDATE_KEY_ACME) for ops' in check['summary']
    assert post(api,f"credentials/{row['id']}",{**rotate,'env':'UPDATE_KEY_ACME'})['env']=='UPDATE_KEY_ACME'
    assert 'reserved_credentials' not in {x['id'] for x in get(api,'health')['checks']}
