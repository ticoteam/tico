# Custom frontend example

A plain HTML and JavaScript app, no build and no dependencies, that signs in to a Tico server and shows
the org chart, chats with a bot (with the reply streaming in), lists tasks and Needs you items, and shows a bot's Files (`GET /api/v2/bots/{bot}/files`).
It is the reference for `docs/custom-frontend.md`, and `ui/tests/custom-frontend.cts` runs it in a browser.

1. On the Tico server, allow this page's origin: `TICO_CORS_ORIGINS=http://localhost:5173` in `.env`,
   then `docker compose up -d`.
2. Put your server's address in `config.js`.
3. `python3 -m http.server 5173` in this directory, and open <http://localhost:5173>.

Start reading at `app.js`; it is one short file in three parts (calls, sign-in, views).
