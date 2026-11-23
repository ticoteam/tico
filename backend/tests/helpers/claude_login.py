"""Synthetic credentials for full Claude login tests."""

import json
import time


def access(n):
    return f"sk-ant-oat01-access-{n}-" + "x" * 24


def refresh(n):
    return f"sk-ant-ort01-refresh-{n}-" + "y" * 24


def full(email="ana@acme.example", n=1, minutes=8 * 60, days=29):
    now = int(time.time() * 1000)
    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": access(n),
                "refreshToken": refresh(n),
                "expiresAt": now + minutes * 60_000,
                "refreshTokenExpiresAt": now + days * 86_400_000,
                "scopes": ["user:inference", "user:profile"],
                "subscriptionType": "max",
            },
            "oauthAccount": {"emailAddress": email, "organizationUuid": "org-acme"},
        }
    )
