/* ui/app/inboxes-alerts.js — Inboxes > Settings > Texts: Alerts, texts to a person's own phone. A placeholder until
   alerts land. Registry: ui/app/inboxes.js.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

inboxesSetting('texts-alerts', {section: 'texts', order: 40,
  render: host => { host.innerHTML = inboxesSoon('inboxes-alerts', 'Alerts'); }});
