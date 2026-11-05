/* ui/app/inboxes-sending.js — Inboxes > Settings > Email: how bots send email. A placeholder until the sending policy
   lands; today's controls stay where they are (each bot's Mail sending in its settings, What Tico may do above).
   Registry: ui/app/inboxes.js.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

inboxesSetting('email-sending', {section: 'email', order: 40,
  render: host => { host.innerHTML = inboxesSoon('inboxes-email-sending', 'Sending'); }});
