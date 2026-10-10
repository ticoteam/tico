/* ui/app/core.js — State-free basics: route constants, $, esc, md, s3url
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

const API = '/api';
const GH = 'https://github.com/ticoteam/tico';
// The building is an optional view of the team; Updates remains the default route.
const OVERVIEW = '#/overview';
const WELCOME = '#/welcome';          // first run: names, the team, the bot templates, a computer
const CHAT = '#/chat';                // retired with the Tico chat: old links land on Tasks
const NOTES = '#/notes';               // legacy route
const RECORDINGS = '#/recordings';    // legacy route
const MEETINGS = '#/meetings';        // imported meeting transcripts: the list, Import, and each meeting
const UPDATES = '#/updates';          // the bots' daily and weekly updates, a feed (backend/updates.py)
const TASKS = '#/tasks';              // hub tasks and Issues in one page (List | Board)
const GOALS = '#/goals';              // the team chart with everyone's goals and a colour
const ASSISTANT = '#/assistant';      // your private chat with the assistant (ui/app/assistant-page.js)
const BOARD = '#/board';              // the old links still work, each opening its view
const ISSUES = '#/issues';
const RECURRING = '#/recurring';      // the Tasks page, opened on the Routines view
const SETTINGS = '#/settings';
const HELP = '#/help';                // a short mental model for humans joining the team
const CREDENTIALS = '#/credentials';   // the owner's connections: what is connected and whether it works
const SQL_PAGE = '#/sql';              // the owner's and admins' read-only query page over the hub database
const DOCS = '#/docs';              // team-wide documentation, separate from bot Docs tabs
const INTEGRATIONS = '#/integrations'; // integrations/*.md: how a bot uses each outside system, its queries, shared learnings
const MAIL = '#/mail';                // server-stored mail copies; #/inbox redirects here
const LEARNINGS = '#/learnings';     // the nightly learning run: what each night changed (ui/app/learnings.js)
const MESSAGING = '#/messaging';       // selected Message bot: setup, schedules, and example messages
// Bot notices still flow through /api/v2/messages?unread=1. People read mail on #/mail, not live Gmail.
const $ = (s, r=document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// A shaped placeholder for a list that is still loading (styles/skeleton.css): `n` rows of uneven width, so the page
// keeps its layout and does not jump when the rows arrive. `card` drops the dividers for a list inside a card.
const SKEL_W = [62, 44, 71, 53, 38, 66, 49, 57];
const skelRows = (n = 5, {card = false, dot = true} = {}) => `<div class="skel${card ? ' skel-card' : ''}" aria-busy="true" aria-label="Loading">${
  Array.from({length: n}, (_, i) => `<div class="skel-row">${dot ? '<span class="skel-dot"></span>' : ''}<span class="skel-bar" style="width:${SKEL_W[i % SKEL_W.length]}%"></span><span class="skel-bar skel-end"></span></div>`).join('')}</div>`;
// A person is typing in `el`: a text field has focus or holds something other than its default. A poll or
// a same-route redraw must leave such a form alone (checkboxes and selects save when changed, so they do not count).
const formBusy = el => !!el && [...el.querySelectorAll('textarea, input:not([type=checkbox], [type=radio], [type=hidden], [type=button], [type=submit])')]
  .some(field => field === document.activeElement || field.value !== field.defaultValue);
// Bot text (final replies, status notes, playbooks) is untrusted: every render goes through safeMd.
const md = s => safeMd(s);
// bucket objects are only reachable through the presign redirect; markdown may name them as s3:// URIs
const s3url = key => `${API}/s3?key=${encodeURIComponent(String(key || '').replace(/^s3:\/\/[^/]+\//, ''))}`;
const mdS3 = md;   // safeMd links s3:// URIs to their view URL (TICO_S3_VIEW_URLS) or shows a file chip
async function copyText(value) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value);
  const area = document.createElement('textarea'); area.value = value; area.style.position = 'fixed'; area.style.opacity = '0';
  const focused = document.activeElement;
  document.body.appendChild(area);
  try {
    area.select();
    if (!document.execCommand('copy')) throw new Error('Clipboard unavailable');
  } finally { area.remove(); focused?.focus({preventScroll:true}); }
}
