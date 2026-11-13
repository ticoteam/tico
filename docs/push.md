# Phone and browser push

Tico can ring you for a call when Tico is hidden or closed: on your Mac or PC through the browser, and on your phone
through the installed app. It uses Web Push, the browser's own push service (Google's for Chrome and Edge, Mozilla's for
Firefox, Apple's for Safari).

## Turn it on

1. Open your own person page and find **Notifications**.
2. Turn on **Browser notifications** and allow notifications when the browser asks.

That browser now takes push. Each other browser you open Tico in with the switch on (and notifications allowed there)
takes it too.

**iPhone and iPad:** push works only in the installed app. In Safari, tap Share, then **Add to Home Screen**, open Tico
from the home screen, and turn the switch on there.

## What is sent

A ring when someone calls you in a direct message or a thread of up to 8 people: the caller's name, the thread's title
and a link to the call. Tapping it opens the thread in Tico (an open Tico tab, or a new one). A ring is dropped if your
device does not get it within a minute.

Each message is encrypted for your browser alone (RFC 8291), so the push service carries it without reading it. Nothing
is sent while Browser notifications are off, to a bot, or from a rehearsal copy of an install.

## Turn it off

Turn **Browser notifications** off in the browser you no longer want rung: that browser stops taking push, and while the
switch is off no browser of yours is rung. You can also block notifications for Tico in the browser's site settings;
when the push service then tells Tico the browser is gone, Tico stops sending to it.

## For admins

The install makes one signing key pair (VAPID, RFC 8292) the first time a browser asks for it. Its private half is
sealed with the server's credential key ([Credentials](credential-vault.md)) in the database and never leaves the
server; the public half is what browsers subscribe with (`GET /api/v2/push/key`). A person's browsers are rows in
`push_subscriptions`, added and removed only by that person, signed in as themselves
(`POST` and `DELETE /api/v2/push/subscriptions`). Pushes go out from the server to the push service over https; an
endpoint on a private or local address is refused.

Each push names a contact for the push service (VAPID's `sub`): the server's public https address, or on an install
without one, the owner's email (`TICO_OWNER_EMAIL`, else the team's owner). With neither, it is the placeholder
`mailto:push@localhost`; Chrome and Firefox take it, and Apple may refuse pushes from such an install, so set an owner
email or a public https address for iPhone and Safari push.
