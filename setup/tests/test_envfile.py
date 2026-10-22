
from setup import envfile
from setup.settings import Settings


def s(**kw):
    base = dict(domain="t.example.com", company="Acme Inc", owner_email="me@example.com", auth="google",
                client_id="cid", client_secret="S3cret$value")
    base.update(kw)
    return Settings(**base)


def test_render_orders_and_omits_empty_and_doubles_dollars():
    text = envfile.render(s().to_env())
    assert 'TICO_TEAM_NAME="Acme Inc"' in text and 'TICO_COMPANY_NAME="Acme Inc"' in text
    assert 'TICO_OIDC_CLIENT_SECRET="S3cret$$value"' in text
    assert "COMPOSE_PROFILES=caddy,updater" in text
    assert "TICO_TAG" not in text and "CLOUDFLARE_TUNNEL_TOKEN" not in text
    assert text.index("TICO_COMPANY_NAME") < text.index("TICO_AUTH_PROXY")


def test_redact_masks_every_secret_key_and_only_those():
    x = s(decisions_provider="openai", decisions_key="sk-abc123", front_door="cloudflared", tunnel_token="tok-999",
          inbound_key="tin-private-123", inbound_url="https://inbox.private.example")
    red = envfile.redact_env(envfile.render(x.to_env()))
    for secret in ("S3cret", "sk-abc123", "tok-999", "tin-private-123", "https://inbox.private.example"):
        assert secret not in red
    assert "TICO_OIDC_CLIENT_ID=cid" in red and red.count("********") == 5


def test_scrub_removes_secret_values_from_free_text():
    assert envfile.scrub("boom tok-999 boom", ["tok-999"]) == "boom ******** boom"


def test_runner_hostname_sets_the_runner_url_for_a_tunnel_only():
    tunnel = dict(front_door="cloudflared", auth="cloudflare", tunnel_token="tok")
    assert s(**tunnel, runner_host="runner.t.example.com").to_env()["TICO_RUNNER_URL"] == "https://runner.t.example.com"
    assert "TICO_RUNNER_URL" not in s(**tunnel, runner_host="none").to_env()
    assert "TICO_RUNNER_URL" not in s(**tunnel).to_env()
    assert "TICO_RUNNER_URL" not in s(runner_host="runner.t.example.com").to_env()     # caddy


def test_runner_hostname_defaults_to_a_name_the_universal_certificate_covers():
    from setup.settings import default_runner_host
    assert default_runner_host("example.com", "example.com") == "runner.example.com"
    assert default_runner_host("tico.example.com", "example.com") == "tico-runner.example.com"
    assert default_runner_host("tico.example.com") == "runner.tico.example.com"      # zone unknown


def test_the_runner_hostname_comes_back_from_the_saved_env():
    from setup.settings import from_saved
    env = envfile.render(s(front_door="cloudflared", runner_host="r.example.com").to_env())
    restored = from_saved({"domain": "t.example.com", "front_door": "cloudflared"}, env)
    assert restored.runner() == ""  # A passive private endpoint is not permission to create/repoint DNS.
    assert restored.effective_runner_host() == "r.example.com"


def test_named_runtime_roundtrip_preserves_private_secret_tombstones_and_resource_identity(monkeypatch):
    import json
    from setup import state
    from setup.settings import RUNTIME_ENV, from_saved
    from setup.ui import MissingInput
    import pytest
    values = {"TICO_PUBLIC_URL": "https://t.example.com", "TICO_HQ_URL": "http://hq.acme.example:8080/",
              "TICO_DOMAIN_ALIASES": "old.example.com", "TICO_CORS_ORIGINS": "https://old.example.com",
              "TICO_VISUAL_ORIGIN": "https://files.acme.example", "TICO_VISUAL_SAME_ORIGIN": "0", "TICO_CADDY_VISUALS": "0",
              "TICO_RUNNER_URL": "http://legacy.acme.example:8081/helper/", "TICO_GATEWAY_URL": "http://gateway.acme.example:8082/v1/",
              "TICO_VISUAL_SECRET": "fictional-visual-$-secret", "TICO_TRUSTED_PROXIES": "127.0.0.1/32,172.30.0.0/29",
              "TICO_CLIENT_IP_HEADER": "X-Forwarded-For", "TICO_EDGE_SUBNET": "172.30.0.0/29",
              "TICO_EDGE_DYNAMIC_RANGE": "172.30.0.4/30", "TICO_CADDY_ADDRESS": "172.30.0.2", "TICO_CLOUDFLARED_ADDRESS": "172.30.0.3",
              "HQ_PRIVATE_KEY": "not-imported", "UNRECOGNIZED": "not-imported"}
    monkeypatch.setenv("TICO_HQ_URL", "https://ambient-not-imported.acme.example")
    monkeypatch.setenv("TICO_VISUAL_SECRET", "ambient-secret-not-imported")
    saved = {**s(front_door="cloudflared").public(), "dns_name": "pv-acme.preview.example", "dns_zone": "preview.example",
             "tunnel_id": "t-fictional", "aws_instance_id": "i-fictional"}
    for field in RUNTIME_ENV: saved.pop(field, None)  # Public state from before these named fields existed.
    restored = from_saved(saved, envfile.render(values))
    # Aliases deliberately require the stricter root runner shape before they can be routed.
    with pytest.raises(MissingInput, match="runner origin"):
        restored.public()
    restored.domain_aliases = ""
    for _ in range(2):
        public, env = restored.public(), envfile.render(restored.to_env())
        assert "fictional-visual-$-secret" not in json.dumps(public) and "visual_secret" not in public
        assert "fictional-visual-$-secret" not in envfile.redact_env(env)
        assert envfile.scrub("failed fictional-visual-$-secret", restored.secrets()) == "failed ********"
        assert public['dns_name'] == saved['dns_name'] and public['dns_zone'] == saved['dns_zone']
        assert public['tunnel_id'] == 't-fictional' and public['aws_instance_id'] == 'i-fictional'
        assert restored.runner() == '' and restored.effective_runner_host() == 'legacy.acme.example'
        state.save(restored.domain, public, env)
        private = state.home() / restored.domain / 'env'
        assert private.stat().st_mode & 0o777 == 0o600
        restored = from_saved(*state.load(restored.domain))
        emitted = restored.to_env()
        assert all(emitted[key] == value for key, value in values.items() if key in RUNTIME_ENV.values() and key != 'TICO_DOMAIN_ALIASES')
        assert emitted['TICO_VISUAL_SECRET'] == values['TICO_VISUAL_SECRET']
        assert 'HQ_PRIVATE_KEY' not in emitted and 'UNRECOGNIZED' not in emitted
    # Saved managed DNS identity remains separate when an older private env selects another effective endpoint.
    restored.runner_host = 'owned.example.com'
    assert restored.runner() == 'owned.example.com' and restored.effective_runner_host() == 'legacy.acme.example'
    assert restored.to_env()['TICO_RUNNER_URL'] == values['TICO_RUNNER_URL']
    # A poisoned public secret can never replace the private signing identity.
    assert from_saved({**restored.public(), 'visual_secret': 'forged-public-secret'}, env).visual_secret == values['TICO_VISUAL_SECRET']


def test_explicit_runtime_clears_cannot_resurrect_and_caddy_needs_explicit_opt_in():
    from setup.cli import parser
    from setup.settings import from_saved, gather_runtime
    from setup.ui import MissingInput
    import pytest
    old = {'TICO_DOMAIN_ALIASES': 'old.example.com', 'TICO_CORS_ORIGINS': 'https://old.example.com',
           'TICO_PUBLIC_URL': 'https://t.example.com', 'TICO_HQ_URL': 'https://hq.acme.example',
           'TICO_VISUAL_ORIGIN': 'https://files.acme.example', 'TICO_VISUAL_SAME_ORIGIN': '0', 'TICO_CADDY_VISUALS': '0'}
    base = s(front_door='cloudflared').public()
    for name in ('domain_aliases','cors_origins','public_url','hq_url','visual_origin','visual_same_origin','caddy_visuals'):base.pop(name)
    restored = from_saved(base, envfile.render(old))
    args = parser().parse_args(['--domain-aliases', '', '--cors-origins', '', '--public-url', '', '--hq-url', '', '--visual-origin', '',
                                '--visual-same-origin', '0', '--caddy-visuals', '0'])
    gather_runtime(args, restored, final=True)
    for _ in range(2):
        restored = from_saved(restored.public(), envfile.render(old))
        assert all(getattr(restored, field) == '' for field in ('domain_aliases','cors_origins','public_url','hq_url','visual_origin'))
        assert restored.visual_same_origin == restored.caddy_visuals == '0'
    external = s(visual_origin='https://cdn.acme.example')
    assert external.to_env().get('TICO_CADDY_VISUALS', '') == ''
    external.caddy_visuals = '1'
    assert external.to_env()['TICO_CADDY_VISUALS'] == '1'
    # Caddy accepts a validated loopback endpoint; only alias mode requires a DNS runner hostname.
    for url in ('http://127.0.0.1:8765/', 'https://[::1]:8765/'):
        external.runner_url, external.visual_same_origin = url, '0'
        assert external.to_env()['TICO_RUNNER_URL'] == url
        assert external.to_env()['TICO_CADDY_VISUALS'] == '1'
        external.front_door, external.caddy_visuals = 'cloudflared', '0'
        external.domain_aliases, external.cors_origins = 'old.example.com', 'https://old.example.com'
        with pytest.raises(MissingInput, match='plain runner origin'):
            external.to_env()
        external.front_door, external.caddy_visuals, external.domain_aliases = 'caddy', '1', ''
    external.runner_url = 'http://127.0.0.1:8765/not-root'
    with pytest.raises(MissingInput, match='root runner URL'):
        external.to_env()
    external.runner_url, external.visual_same_origin = '', ''
    external.front_door = 'cloudflared'
    with pytest.raises(MissingInput, match='needs Caddy'):
        external.to_env()
    external.front_door = 'caddy'
    external.runner_url = 'https://cdn.acme.example'
    with pytest.raises(MissingInput, match='distinct from app and runner'):
        external.to_env()
    external.runner_url = ''
    external.visual_origin = ''
    with pytest.raises(MissingInput, match='separate visual origin'):
        external.to_env()
    external.caddy_visuals = '0'
    assert external.to_env()['TICO_CADDY_VISUALS'] == '0' and external.visual_same_origin == ''
