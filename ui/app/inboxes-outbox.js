/* ui/app/inboxes-outbox.js — Letters out: letters Tico sends by post. A placeholder until sending letters lands: no
   rows yet, a Coming soon card in Inboxes > Settings > Letters out and in first run. Registry: ui/app/inboxes.js.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

inboxesKind('outbox', {group: 'letters-out', label: 'Letters out', order: 50,
  rows: () => [],
  detail: async (host, id) => {
    host.innerHTML = inboxesHead(host, {name: 'Letters out', status: 'off'})
      + inboxesSoon('inbox-outbox', 'Letters out');
    return {kind: 'outbox', id, name: 'Letters out', address: ''};
  }});

inboxesSetting('letters-out', {section: 'letters-out', order: 10,
  render: host => { host.innerHTML = inboxesSoon('inboxes-letters-out', 'Letters out'); }});

inboxesStep('letters-out', {order: 40, title: 'Send letters', optional: true,
  done: () => false,
  render: host => { host.innerHTML = '<p><button type="button" class="ghost" disabled>Connect Click2Mail</button> <span class="pill">Coming soon</span></p>'; }});
