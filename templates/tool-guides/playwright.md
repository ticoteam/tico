---
name: Playwright
category: engineering
summary: "Drive a browser from a script: test a page, take screenshots, read what it shows"
commands: [playwright]
---
## What it is for
Checking a web page works the way a person would use it, and taking screenshots for proof.

## How to use it

    npx playwright test
    npx playwright screenshot --full-page https://app.acme.example/pricing pricing.png

## Good to know
- For a site a bot signs in to, use Tools' website login (`tico browser`), not a password in a script.
