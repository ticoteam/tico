"""Google Ads and Meta Ads as known tool services (backend/tool_services.py): the keys TIDY stores land on them, Brex's
and Mercury's own variable spellings land on those tools, and the data migration gives a tool made before the two
services existed its service."""

import importlib
import sqlite3
from pathlib import Path

import pytest

from backend import tool_services
from backend.config import Settings
from backend.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "schema_old_main.sql"
AD_SERVICES = importlib.import_module("backend.migrations.20261106070714_ad_tool_services")


@pytest.mark.parametrize("env,name,service", [
    ("META_ACCESS_TOKEN", "Meta · META_ACCESS_TOKEN", "meta-ads"),
    ("META_ADS_ACCESS_TOKEN", "", "meta-ads"),
    ("FACEBOOK_ACCESS_TOKEN", "", "meta-ads"),
    ("", "Facebook Ads token", "meta-ads"),
    ("GOOGLE_ADS_CLIENT_ID", "Google Ads · GOOGLE_ADS_CLIENT_ID", "google-ads"),
    ("GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_CLIENT_SECRET (paid-rental-ads)", "google-ads"),
    ("GOOGLE_ADS_REFRESH_TOKEN", "", "google-ads"),
    ("GOOGLE_ADS_DEVELOPER_TOKEN", "", "google-ads"),
    ("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "", "google-ads"),
    ("GOOGLE_ADS_CUSTOMER_ID", "", "google-ads"),
    ("BREX_TOKEN", "", "brex"),
    ("MERCURY_API_TOKEN", "", "mercury"),
])
def test_the_keys_land_on_their_service(env, name, service):
    assert tool_services.match(env=env, name=name) == service


def test_other_google_keys_are_not_google_ads():
    assert tool_services.match(env="GOOGLE_API_KEY") != "google-ads"
    assert tool_services.match(env="GOOGLE_MAPS_API_KEY") != "google-ads"


def test_meta_is_checked_with_a_bearer_call_and_google_ads_stays_on_computers():
    meta = tool_services.defaults("meta-ads")
    assert (meta["base_url"], meta["auth_scheme"], meta["check_path"]) == \
        ("https://graph.facebook.com/v26.0", "bearer", "/me")
    google = tool_services.defaults("google-ads")
    assert google["name"] == "Google Ads" and google["base_url"] == "" and google["check_path"] == ""


def test_the_migration_gives_an_old_ad_tool_its_service_and_leaves_the_rest(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(FIXTURE.read_text())
    conn.commit()
    conn.close()
    Store(Settings(db_path=path)).initialize()
    conn = sqlite3.connect(path)
    now = "2026-11-05T00:00:00Z"
    tools = (("google-ads-client-id", "GOOGLE_ADS_CLIENT_ID", "computer"), ("meta-token", "META_ACCESS_TOKEN", "proxy"),
             ("acme-internal", "ACME_TOKEN", "computer"))
    for tid, env, delivery in tools:
        conn.execute("INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
                     "updated_at) VALUES(?,?,'','','bearer','',?,'human:ana',?,?)", (tid, env, delivery, now, now))
        conn.execute("INSERT INTO credentials(id,name,kind,env,preview,ciphertext,nonce,source,created_at,updated_at,"
                     "updated_by,created_by,tool_id) VALUES(?,?,'api_key',?,'',?,?,'',?,?,'human:ana','human:ana',?)",
                     ("k-" + tid, env, env, b"x", b"n", now, now, tid))
    conn.execute("INSERT INTO tools(id,name,service,base_url,auth_scheme,check_path,delivery,created_by,created_at,"
                 "updated_at) VALUES('close-crm','Close CRM','close','https://api.close.com','basic_user','/x',"
                 "'computer','human:ana',?,?)", (now, now))
    AD_SERVICES.up(conn)
    AD_SERVICES.up(conn)                                        # idempotent
    got = {r[0]: r[1:] for r in conn.execute("SELECT id, service, base_url, check_path, delivery, name FROM tools")}
    conn.close()
    assert got["google-ads-client-id"] == ("google-ads", "", "", "computer", "GOOGLE_ADS_CLIENT_ID")
    assert got["meta-token"] == ("meta-ads", "https://graph.facebook.com/v26.0", "/me", "proxy", "META_ACCESS_TOKEN")
    assert got["acme-internal"][0] == ""
    assert got["close-crm"][:3] == ("close", "https://api.close.com", "/x")


def test_the_migration_skips_a_database_without_tools(tmp_path):
    conn = sqlite3.connect(tmp_path / "empty.sqlite")
    AD_SERVICES.up(conn)
    conn.close()
