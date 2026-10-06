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

