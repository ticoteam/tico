#!/usr/bin/env python3
"""Fetch the public documents in SOURCES into docs/<slug>.txt (plain text, capped at 60,000 characters) and write each
document's JSON header (url, fetched_at, from, truncated) into <slug>.json, keeping any expected obligations already
hand-written there. Run once to refresh the corpus; the eval reads the stored text, not the network.

    python3 evals/counsel/fetch.py [--only slug,slug]
"""
import argparse
import datetime
import html
import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAP = 60_000
UA = "Mozilla/5.0 (compatible; TicoCounselEval/1.0; +https://tico.dev)"
SOURCES = [
    ("twilio-messaging-policy", "https://www.twilio.com/en-us/legal/messaging-policy", "Twilio Messaging Policy", "vendor"),
    ("twilio-aup", "https://www.twilio.com/en-us/legal/aup", "Twilio Acceptable Use Policy", "vendor"),
    ("whatsapp-business-policy", "https://www.whatsapp.com/legal/business-policy", "WhatsApp Business Policy", "vendor"),
    ("meta-platform-terms", "https://developers.facebook.com/terms/", "Meta Platform Terms", "vendor"),
    ("stripe-restricted", "https://stripe.com/legal/restricted-businesses", "Stripe Restricted Businesses", "vendor"),
    ("stripe-ssa", "https://stripe.com/legal/ssa", "Stripe Services Agreement", "vendor"),
    ("google-ads-policies", "https://support.google.com/adspolicy/answer/6008942", "Google Ads Policies", "vendor"),
    ("slack-api-terms", "https://slack.com/terms-of-service/api", "Slack API Terms", "vendor"),
    ("github-aup", "https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies",
     "GitHub Acceptable Use Policies", "vendor"),
    ("aws-customer-agreement", "https://aws.amazon.com/agreement/", "AWS Customer Agreement", "vendor"),
    ("apple-app-review", "https://developer.apple.com/app-store/review/guidelines/", "Apple App Review Guidelines",
     "vendor"),
    ("common-paper-csa", "https://commonpaper.com/standards/cloud-service-agreement/", "Common Paper Cloud Service Agreement",
     "other"),
    ("common-paper-dpa", "https://commonpaper.com/standards/data-processing-agreement/", "Common Paper DPA", "other"),
    ("ftc-can-spam", "https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business",
     "FTC CAN-SPAM Compliance Guide", "government"),
    ("ftc-health-breach", "https://www.ftc.gov/legal-library/browse/rules/health-breach-notification-rule",
     "FTC Health Breach Notification Rule", "government"),
    ("anthropic-aup", "https://www.anthropic.com/legal/aup", "Anthropic Usage Policy", "vendor"),
    ("ecfr-tcpa-64-1200", "https://www.ecfr.gov/current/title-47/chapter-I/subchapter-B/part-64/subpart-L/section-64.1200",
     "47 CFR 64.1200 (TCPA rules)", "government"),
    ("twilio-a2p-10dlc", "https://www.twilio.com/docs/messaging/compliance/a2p-10dlc", "Twilio A2P 10DLC Guide", "vendor"),
    ("hubspot-terms", "https://www.hubspot.com/terms-of-service", "HubSpot Terms of Service", "vendor"),
    ("ecfr-coppa-312", "https://www.ecfr.gov/current/title-16/chapter-I/subchapter-C/part-312",
     "16 CFR 312 (COPPA rule)", "government"),
    ("commonpaper-mutual-nda", "https://commonpaper.com/standards/mutual-nda/", "Common Paper Mutual NDA", "other"),
]


class Text(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form", "button"}
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def to_text(raw, content_type):
    if "html" not in content_type and not raw.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        return raw
    parser = Text()
    parser.feed(raw)
    text = html.unescape("".join(parser.parts))
    text = re.sub(r"[ \t\r\f\v\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=60) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return to_text(response.read().decode(charset, errors="replace"), response.headers.get("Content-Type", ""))


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    args = parser.parse_args(argv)
    only = {s for s in args.only.split(",") if s}
    failed = []
    for slug, url, name, kind in SOURCES:
        if only and slug not in only:
            continue
        try:
            text = fetch(url)
        except Exception as exc:  # network: report and move on, the corpus is refreshed by hand
            failed.append((slug, str(exc)))
            print(f"{slug}: FAILED {exc}")
            continue
        truncated = len(text) > CAP
        text = text[:CAP] if truncated else text
        (HERE / "docs" / f"{slug}.txt").write_text(text + "\n", encoding="utf-8")
        meta_path = HERE / f"{slug}.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"expected": []}
        meta.update({"url": url, "fetched_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "from": {"name": name, "kind": kind}, "truncated": truncated})
        meta.setdefault("expected", [])
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{slug}: {len(text)} characters{' (truncated)' if truncated else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
