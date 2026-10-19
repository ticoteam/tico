# Visuals in replies

A bot or a person attaches a visual to a chat reply or a task comment: a picture, an SVG, a self-contained HTML
page, a video or an audio clip, with a title. This page is the reference: when a bot adds one, how a visual shows in
a conversation, how to attach one, what Tico does to a page when it is attached, the types and limits, who can see
it, and what a reader sees outside Tico.

Pictures, SVG and visual pages show inline as cards. Video and audio play inside their card.

## When a bot adds a visual

Every bot's instructions carry this line, word for word:

> Add a visual (chart, table, diagram, mockup, screenshot, clip or recording) to a reply when it makes the answer
> clearer, never for a plain answer, and keep the text answer complete on its own. For brand assets, designed
> graphics or edited video, file a task for bot:designer or bot:video-producer; do not buy them.

- **Design and Video Producer** (`designer`, `video-producer`) get the first sentence only, since the second sends
  that work to them.
- **The shared rules** say the same under "Visuals in replies" (`policies/shared-rules.md`), and add how to attach
  one: `--visual <file>` and `--title <caption>` on the reply or comment.
- **No switch.** It applies to every bot. There is no per-bot or admin setting to turn it off, and `AGENT.md` doesn't
  need to repeat it.

Keep the text answer complete because not every reader sees the visual: Slack shows only a link to it (see
[Outside Tico](#outside-tico)).

## In a chat or task conversation

A picture (PNG, JPEG, GIF, WebP), an SVG or a visual page (HTML) attached to a chat message or a task comment shows
under the text as a card. This holds for a person's attachments too. The card is the width of the message and never
wider than the screen. A message shows up to 4 cards, in the order attached; the rest stay file chips. Video and
audio are cards too (see [Video and audio](#video-and-audio)) and count toward the 4.

- **Caption.** The visual's title, or its file name when it has none.
- **Menu** (**⋯**, labelled **More**, next to the caption): **Expand**, **Open in file viewer**, **Download**.
- **A visual page** shows at its own height, up to 480 px (60% of the screen on a phone). A taller page is cut
  there with a fade and an **Expand** button. The page can't run its own scripts, open forms, load anything or
  reach the app. A link in it opens a new tab, and only when clicked.
- **Expand** opens the visual full screen; so does a click or tap on a picture, or a tap on a page on a touch
  screen. Pictures zoom: click or tap for 2×,
  pinch or Ctrl + scroll up to 6×. Pages scroll. **Close**, Esc, a swipe down or Back returns you to the same place
  in the conversation.
- **Dark mode.** An SVG or a page sits on a light card, so one that sets no background stays readable.
- **While it loads**, the card holds its space, so the conversation doesn't jump.
- **Versions.** The card keeps the version it was posted with. When the file has a newer version, **Newer version**
  next to the caption opens the newest.
- **"This visual was removed"** shows when the file was removed. The card then has no menu.
- **"Couldn't show this visual"** shows, with **Open in file viewer**, when the visual can't be displayed.

On a task, **Open in file viewer** and **Newer version** open the task's own file panel at that version; see
[Tasks](tasks.md#files-and-questions-on-the-page). In a chat they open the file viewer.

A visual shows as a file chip instead of a card when its type or size isn't one that shows inline (see
[Types and limits](#types-and-limits)), and on a self-hosted server without a visual address (see
[Self-hosting](#self-hosting)).

### Video and audio

A video (MP4, WebM, MOV, M4V) or an audio clip (MP3, M4A, WAV, OGG, AAC) attached to a chat message or a task comment
plays inside its card, a person's attachments too. The caption, menu, versions and "This visual was removed" work as
for the other cards; the menu has no **Expand**.

- **Video** shows its poster when it has one: a frame Tico takes from the clip, or the poster attached with it. The
  player has play/pause, a seek bar, volume and the device's full screen. A video keeps its shape: a portrait clip
  is narrower, not cropped, and no taller than the card's cap.
- **Audio** is a compact player with play/pause and a seek bar.
- **Nothing plays by itself.** A clip starts only when you press play.
- **One clip at a time.** Starting a clip pauses any other clip on the page.
- **A playing clip keeps going** while you scroll and while the conversation updates with new messages.
- **Seeking a long file** starts playback at that point without downloading the whole file first.
- **A format the device can't play** gets no player. The card says why, for example "This device can't play MOV
  video.", with **Open in file viewer** and **Download**. A clip that can't load says "Couldn't load this video.
  Check the connection, or download it." with the same two links.

Which formats play, and how full screen looks, depend on the browser or app you use.

## Attach a visual

**CLI.** Add `--visual PATH --title "…"` to `hub message send`, `hub chat send` or `hub task comment`. Repeat
`--visual` for more than one; each `--title` goes with the `--visual` in the same place in the list.

```
hub task comment <task id> "Signups by week" --visual reports/signups.png --title "Signups, last 12 weeks"
hub message send human:ana "Two drafts" --visual art/a.svg --title "Draft A" --visual art/b.svg --title "Draft B"
```

`PATH` is a file in your bot's checkout, with the same checks as [publishing a file](files.md#publishing): no path
outside the checkout, no symbolic links, no credential-like names. `ID@VERSION` (for example `f_7k2@3`) names a file
already in Tico instead: a file on that task, or in that conversation for a chat message. A `--title` with no
`--visual` is refused. A visual with no title shows its file name.

**MCP.** `hub_message_send`, `hub_chat_send` and `hub_task_comment` take `visuals`, a list of up to 10:
`{"path": "reports/signups.png", "title": "…"}` for a file in the checkout, or `{"file": "<file id>@<version>",
"title": "…"}` for a file already in Tico. A `path` works only from the hub tools on your bot's Computer.

**API.** `POST /api/v2/messages` and `POST /api/v2/tasks/{id}/comments` take `attachments`, up to 10 items in order.
Each is `"<file id>@<version>"` or `{"ref": "<file id>@<version>", "title": "…"}`. Upload the file first: to the task
with `POST /api/v2/tasks/{id}/files`, or, from a bot's run, with `POST /api/v2/files/uploads` (it lands in the
conversation the run is in). A person's chat upload, `POST /api/v2/uploads/chat/{bot}`, takes `titles`, one per
file in upload order. A title is at most 200 characters. The schema is `VisualRef` in `docs/openapi/v2.json`.

## What gets stored

The visual is a normal versioned file on that conversation or task, listed with its other [files](files.md). The
message keeps three things: the file, the version it was posted with, and the title. Reading the message returns
them, all attachments in the order given, each with a `kind`: `image`, `svg`, `page`, `video`, `audio`, or `file`
for one that shows as a file chip. A later version of the file does not change what the message shows.

## What gets embedded in a page

For an HTML page or an SVG attached from your checkout, the hub tools on your Computer first embed the local images
and fonts it refers to (PNG, JPEG, GIF, WebP, SVG, WOFF, WOFF2, TTF, OTF), read from the page's own folder in the
checkout. They come from `src`, `href`, `poster`, `xlink:href` and `srcset` values and CSS `url()`. Each embedded
file is at most 10 MB and passes the same checkout checks as the page.

When the page is attached, Tico removes everything that would load from somewhere else:

- **Remote references** (an `https://` image, a stylesheet, an `@import`, a font on a CDN) are not embedded and not
  fetched. They are removed, and the result lists each one.
- **Scripts don't run.** `<script>`, frames, `<object>`, `<embed>`, `<noscript>`, inline event handlers (`onclick`)
  and meta refresh are dropped. The page is static.
- Links (`<a href>`) stay. In the card, a link opens a new tab, and only when clicked.

If anything was removed, Tico saves the cleaned page as a new version of the file, and that version is the one
posted. The stored visual never loads anything from the network.

The result tells you what happened:

- `visuals`: one entry per attachment, with its `ref` (the version posted), its `kind`, and `unembedded`, the
  references removed from it.
- `visuals_local`: for each page attached from a path, the references the hub tools on your Computer could not
  embed: remote addresses, and local files that are missing, too big or refused.

To keep an image in the page, put the file in the checkout and refer to it by a relative path, or embed it yourself
as a `data:` URI.

**Size pages by their content, not `100vh`.** The card follows the page's own height. A page sized to the screen
(`height` or `min-height: 100vh`) fills the card up to the cap and is cut there with **Expand**.

## Types and limits

| Kind | Types | Largest that shows inline |
|---|---|---|
| Picture | PNG, JPEG, GIF, WebP | 25 MB |
| SVG | SVG | 10 MB |
| Page | HTML | 10 MB |
| Video | MP4, WebM, MOV, M4V | 2 GB |
| Audio | MP3, M4A, WAV, OGG, AAC | 2 GB |

Today's upload paths cap a file well below the video and audio limit: a bot's `--visual` file is at most 25 MB, and
a person's upload is at most 10 MB.

Any other type, or a file over its limit, posts as a normal file chip, never as an inline card. So does a picture
whose bytes don't match its type (a `.png` that isn't a PNG).

## Who sees it

Exactly the people who can read that conversation or task, as for [files](files.md#visibility). Every request for
a visual checks again that the reader still has access, so someone taken off a private task loses it at once.
Visuals from a private task never show to guests or on the public roadmap.

A visual is shown from a signed address made for one reader. It works only for that reader and expires after at
most 2 hours. Each read of the message returns a current one, which stays the same for up to an hour.

## Outside Tico

Nothing visual leaves Tico. Where a message with visuals is posted to Slack, Slack shows its text and one more line:
**1 visual - view in Tico** (or **N visuals - view in Tico**), linking to that message in Tico.

- **A bot's reply** in a Slack thread gets the line under its text, before `(sent from <bot>)`. A long reply is cut,
  and the line stays after the cut.
- **A task-done post** gets the line when the task owner's result has visuals: the owner's newest comment with
  visuals since the task was last marked done or declined. The line links to that comment and goes before
  **Open task**.
- **What counts.** Pictures, SVG, visual pages, video and audio. Plain files don't count, and a message with none of
  these gets no line.
- **Where the link goes.** A reply on a task opens the task with that comment in view and briefly marked. A chat
  reply opens that bot's chat in the room the reply is in (each Slack thread is a room of its own), at the message.
  If the message isn't there, Tico says so.
- **Who can open it.** Only people who can read that conversation. The link names the message, not its content;
  access is checked when it is opened. Messages on a private task are never sent to Slack.

Slack is the only place outside Tico that gets this line.

## Self-hosting

Visuals are shown from a separate site, apart from the app's sign-in and cookies. Set these in `.env`; `compose.yaml`
passes them to the server, and `.env.example` lists them.

| Setting | What it does |
|---|---|
| `TICO_VISUAL_ORIGIN` | The address visuals are shown from, for example `https://visuals.example.com`. It must be a separate site of its own: never the app's address (`TICO_PUBLIC_URL`), nor a subdomain that shares its cookies. The server refuses to start if it equals the app's address. Any other bad value never stops it: one that is not a URL at all counts as unset (file chips, with a warning in the server log), and with a Cloudflare Tunnel a value the tunnel config can't use (not https, a path, an IP address, the runner's hostname) leaves out the visuals route with a warning ([Cloudflare Tunnel](install.md#cloudflare-tunnel)). |
| `TICO_VISUAL_SECRET` | Optional. The key that signs visual addresses, at least 32 characters. Unset, Tico makes one in a file next to the database on first use. |
| `TICO_VISUAL_SAME_ORIGIN` | `1` serves visuals from the app's own address instead, with the page sandboxed. A local install always does this. |

Without `TICO_VISUAL_ORIGIN`, a production server keeps running and shows visuals as file chips. Messages still keep
their visuals, so they show once you set it.

`TICO_VISUAL_ORIGIN`'s host must reach the same Tico server as the app: visual addresses are
`<TICO_VISUAL_ORIGIN>/visuals/<token>`, and the server answers that path.
