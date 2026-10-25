# Recipe: GitHub webhooks wake a bot

You will make Tico wake a bot whenever someone opens an issue in your GitHub organization. It takes about ten minutes.
The background is on [Webhooks](webhooks.md).

You need: owner or admin rights in the GitHub organization, a bot to wake (this page uses one called `ops`), and a server GitHub
can reach. If yours is a laptop or sits behind a firewall, use an [address through Tico Gateway](webhooks.md#through-tico-gateway)
in step 1 instead of the server's own address.

## 1. Make the incoming webhook in Tico

In Settings → Webhooks → Incoming, add a webhook named *GitHub issues* with the id `github`. Or:

```
hub trigger create "GitHub issues" --id github
```

Copy the address it shows, `https://tico.example.com/api/v2/hooks/github/<secret>`. It is shown once. (For the Gateway address:
`hub trigger relay create github`.)

## 2. Turn on signature checking

```
hub trigger sign github --preset github
```

Tico prints a signing secret once. Copy it. This makes Tico refuse any call that GitHub did not sign with that secret.

## 3. Add the webhook in GitHub

In GitHub: your organization → Settings → Webhooks → Add webhook.

- **Payload URL**: the address from step 1.
- **Content type**: `application/json`.
- **Secret**: the signing secret from step 2.
- **Which events**: *Let me select individual events*, then tick **Issues** only.
- Leave **Active** on, and save.

GitHub sends a `ping` straight away. Tico accepts it and finds no rule for it, so nothing starts. You will see it in
**Recent calls** under the webhook, which also tells you the address and signature work. A red mark in GitHub's *Recent
Deliveries* means Tico refused it; the answer GitHub shows says why (401 is a wrong secret, 404 a wrong address).

## 4. Read the action, not the event

GitHub puts the kind of event in a header (`Issues`) and what happened in the body (`"action": "opened"`). Tico's `github`
preset reads the header. To act on *opened* only, read the body instead:

```
hub trigger update github --type-from /action
```

Because this webhook only gets Issues events, the type is now one of `opened`, `closed`, `reopened`, `edited`, `labeled` and so on.

## 5. Add the rule

In Settings → Rules, add a team rule on the event *Webhook received*, or:

```
hub rule set --scope-kind team --event webhook.received \
  --when '{"trigger":"github","type":"opened"}' \
  --then action \
  --params '{"action":"wake_bot","bot":"ops","text":"New issue in {{data.repository.full_name}}: {{data.issue.title}}\n{{data.issue.html_url}}"}'
```

Only issues that are opened wake `ops`. Every other call is logged and does nothing.

If you would rather keep GitHub's own event name, leave step 4 out, use `"type":"issues"` in the rule, and put
`{{data.action}}` in the text so the bot sees which action it was.

## 6. Try it

Open an issue in a repository of the organization. In a few seconds `ops` is woken with a notice in its room that holds your text
and, fenced as data, the start of the call. If it does not wake:

- Open **Recent calls**. No row means GitHub never reached Tico. A refused row means the signature or address is wrong.
- A row marked accepted with no result means no rule matched. Open it with **View payload** and compare `type` and `trigger` to the
  rule.
- Fix the rule, then use **Run again** on that row. Do not use GitHub's *Redeliver*: it reuses the delivery id, which Tico treats
  as a duplicate for 30 days.

## Variations

- Wake the bot only for one repository's issues: add the repository to the bot's text and read it in the notice, or make the
  webhook on the repository instead of the organization.
- Start a task instead of a wake: set a routine `--on webhook.github`. See [Routines](routines.md). A routine opens a task for
  every call, so keep the GitHub event list short.
- Read a long body: the bot runs `hub trigger delivery github <delivery> --part 2`.
