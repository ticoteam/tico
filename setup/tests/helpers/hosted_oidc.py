"""Helpers that other tests share, moved out of test_hosted_oidc.py so they import no test file."""

ISSUER = "https://accounts.example"
OIDC = ["--auth", "oidc", "--issuer", ISSUER, "--client-id", "install-client-1", "--signin-label", "Tico account"]
