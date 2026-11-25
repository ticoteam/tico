---
name: curl
category: engineering
summary: "Call any HTTP API or fetch a page from the command line"
commands: [curl]
---
## What it is for
One-off calls to an API and checking what an address answers.

## How to use it

    curl -sS -H "Authorization: Bearer $LINEAR_API_KEY" https://api.linear.app/graphql -d '{"query":"{ viewer { id } }"}'
    curl -sSI https://www.acme.example/

## Good to know
- Use the variable Tools gives your run, by its name; a key's value never goes in a command or a file.
- For a tool Tico proxies, use `tico tool call` instead: Tico adds the key.
