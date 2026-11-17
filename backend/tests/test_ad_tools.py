"""Google Ads and Meta Ads as known tool services (backend/tool_services.py): the keys TIDY stores land on them, and
Brex's and Mercury's own variable spellings land on those tools."""

import pytest

from backend import tool_services


@pytest.mark.parametrize(
    "env,name,service",
    [
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
    ],
)
def test_the_keys_land_on_their_service(env, name, service):
    assert tool_services.match(env=env, name=name) == service


def test_other_google_keys_are_not_google_ads():
    assert tool_services.match(env="GOOGLE_API_KEY") != "google-ads"
    assert tool_services.match(env="GOOGLE_MAPS_API_KEY") != "google-ads"
