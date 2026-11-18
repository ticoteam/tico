# Vendor guides

One file a vendor: how a person makes its credential, the read-only options, the auth shape, a cheap check and what
401 and 403 mean there, which Tico route fits (an API tool on the proxy, an MCP server by token or sign-in, a website
tool, on computers) and its pitfalls. Each cites the vendor docs it was written from, with the date. Website sign-ins
in general (2FA, SSO, passkeys, captchas) are `../logins.md`; how each auth shape is handed to Tico is
`docs/auth-types.md` (`tico doc read manual:auth-types`).

## Layout
- `knowledge/vendors/<vendor>.md`: shipped with Tico and replaced by its updates; never edit one. `<vendor>` is the
  vendor id `tico tool index` shows for the tool (`google`, `meta`, `close-crm`, `hugging-face`). A vendor with many
  products has an index (`google.md`) and product guides (`google-ads.md`).
- `knowledge/vendors/learned/<vendor>.md`: general facts about a vendor learned in this company, true for anyone (a
  scope's real name, a path that needs an admin key). The Tools Owner's own; updates never touch it. A vendor with no
  shipped guide gets its guide here, in the same sections.
- `knowledge/company/<vendor>.md`: this company's own notes on a vendor: which account, who owns the login, which key
  does what, how people sign in, decisions. Never a value.
- `knowledge/company/usage.md`: who uses which tool and how, recurring errors and their fixes, tools nobody uses
  (`playbooks/learn-from-use.md`).

Read all three vendor files before connecting, fixing or explaining a tool. `skills/learn-tools/SKILL.md` says how they
are written. A line marked "(unconfirmed)" was not in the vendor's docs when written: trust a real check over it.

## Guides
- `airtable.md`: Airtable
- `anthropic.md`: Anthropic API
- `atlassian.md`: Atlassian (Jira and Confluence Cloud)
- `aws.md`: AWS
- `brex.md`: Brex
- `bright-data.md`: Bright Data
- `calendly.md`: Calendly
- `close-crm.md`: Close CRM
- `cloudflare.md`: Cloudflare
- `elevenlabs.md`: ElevenLabs
- `gemini.md`: Gemini API
- `geocodio.md`: Geocodio
- `github.md`: GitHub
- `google-ads.md`: Google Ads API
- `google-analytics.md`: Google Analytics 4 Data API
- `google-search-console.md`: Google Search Console API
- `google-workspace.md`: Google Workspace (Gmail, Calendar, Drive, admin)
- `google.md`: Google (index)
- `heygen.md`: HeyGen
- `hubspot.md`: HubSpot
- `hugging-face.md`: Hugging Face
- `intercom.md`: Intercom
- `linear.md`: Linear
- `mercury.md`: Mercury
- `meta.md`: Meta (Marketing API and Pages)
- `microsoft-365.md`: Microsoft 365 / Microsoft Graph
- `notion.md`: Notion
- `openai.md`: OpenAI
- `openrouter.md`: OpenRouter
- `peec.md`: Peec AI
- `posthog.md`: PostHog
- `postiz.md`: Postiz
- `quickbooks.md`: QuickBooks Online
- `raygun.md`: Raygun
- `rentcast.md`: RentCast
- `sendgrid.md`: SendGrid
- `sentry.md`: Sentry
- `shopify.md`: Shopify
- `slack.md`: Slack
- `stripe.md`: Stripe
- `supabase.md`: Supabase
- `twilio.md`: Twilio
- `vercel.md`: Vercel
