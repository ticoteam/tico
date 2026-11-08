/* The credential card: a bot needs a secret, so it asks for it inside the chat. The person types it here and
   it goes straight to Credentials (POST /api/v2/credential-requests/{id}/save); the bot and the model never see
   the value. A message with `refs.credential_request` has a host element (`data-credential-host`) that
   window.credentialCards.mount(root, {get, post, esc, toast, reload}) fills from the request.
   The value lives only in the input (and in `typed` while the chat repaints around it): never in storage, a URL,
   a toast, the console or an error message. */
'use strict';
(function () {
  const css = `
.cc-card{align-self:flex-start;width:min(560px,100%);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:6px 0;background:var(--surface)}
.cc-card b{display:block;margin-bottom:6px}
.cc-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.cc-row input{flex:1 1 220px;min-width:0;min-height:44px;font-size:16px}
.cc-row button{min-height:44px}
.cc-help{font-size:12.5px;text-decoration:underline;text-underline-offset:2px;white-space:nowrap}
.cc-note{font-size:12.5px;color:var(--muted);margin:6px 0 0}
.cc-err{font-size:12.5px;color:var(--fail);margin:6px 0 0}
.cc-done{font-size:12.5px;color:var(--muted);padding:6px 0}`;

  const typed = new Map();      // request id -> what was typed so far, so a repaint of the chat keeps it
  const settled = new Map();    // request id -> its final view

  function style() {
    if (document.getElementById('cc-style')) return;
    const el = document.createElement('style');
    el.id = 'cc-style';
    el.textContent = css;
    document.head.appendChild(el);
  }

  // A local check that never echoes the value: what is wrong, or ''.
  function problem(raw, format) {
    const value = String(raw || '').trim();
    if (!value) return 'Enter the value.';
    if (/\s/.test(value)) return 'No spaces or line breaks.';
    if (String(format || '').includes(':')) {
      const at = value.indexOf(':');
      if (at < 1 || at === value.length - 1) return 'Use this format: ' + format;
    }
    return '';
  }

  function view(v, esc) {
    if (v.status === 'saved') return `<div class="cc-done" data-status="saved">Saved. ${esc(v.label || v.env)} is set for ${esc(v.bot_name || v.bot)}.</div>`;
    if (v.status === 'canceled') return '<div class="cc-done" data-status="canceled">Not now.</div>';
    const help = /^https:\/\//i.test(v.help_url || '')
      ? `<a class="cc-help" href="${esc(v.help_url)}" target="_blank" rel="noopener noreferrer">Get one</a>` : '';
    const title = `<b>${esc(v.title || (v.bot_name || v.bot) + ' needs ' + (v.label || v.env))}</b>`;
    if (!v.can_save) return `<div class="cc-card" data-status="pending">${title}<p class="cc-note">${esc(v.note || '')}</p></div>`;
    return `<div class="cc-card" data-status="pending">${title}
      <form class="cc-row" data-cc-form autocomplete="off">
        <input type="password" name="value" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
          data-lpignore="true" data-1p-ignore data-form-type="other" placeholder="${esc(v.format || '')}" aria-label="${esc(v.env)}">
        ${help}
        <button class="primary" type="submit">Save</button>
        <button class="ghost" type="button" data-cc-cancel>Not now</button>
      </form>
      <p class="cc-err" data-cc-error role="alert" hidden></p>
      <p class="cc-note">Goes straight to Credentials. The bot never sees it.</p></div>`;
  }

  async function mount(root, deps) {
    if (!root) return;
    style();
    const {get, post, esc, toast, reload} = deps;
    const paint = (host, v) => {
      const id = v.id || host.dataset.credentialHost;
      if (v.status !== 'pending') { settled.set(id, v); typed.delete(id); }
      host.innerHTML = view(v, esc);
      const form = host.querySelector('[data-cc-form]');
      if (!form) return;
      const input = form.elements.value, error = host.querySelector('[data-cc-error]');
      const show = text => { error.textContent = text; error.hidden = !text; };
      if (typed.has(id)) input.value = typed.get(id);
      input.oninput = () => { typed.set(id, input.value); show(''); };
      form.onsubmit = async event => {
        event.preventDefault();
        const bad = problem(input.value, v.format);
        if (bad) { show(bad); input.focus(); return; }
        const buttons = form.querySelectorAll('button');
        buttons.forEach(b => { b.disabled = true; });
        try {
          const saved = await post(`/v2/credential-requests/${encodeURIComponent(id)}/save`, {value: input.value.trim()});
          input.value = '';
          paint(host, saved);
          reload?.();
        } catch (e) {
          buttons.forEach(b => { b.disabled = false; });
          // The server's words about the format, never the value.
          if ((e?.body?.error?.code || e?.code) === 'format') show(e.message || 'That does not look right.');
          else toast?.(e?.message || 'Could not save', true);
          input.focus();
        }
      };
      host.querySelector('[data-cc-cancel]').onclick = async () => {
        try { paint(host, await post(`/v2/credential-requests/${encodeURIComponent(id)}/cancel`, {})); reload?.(); }
        catch (e) { toast?.(e?.message || 'Could not cancel', true); }
      };
    };
    for (const host of root.querySelectorAll('[data-credential-host]')) {
      const id = host.dataset.credentialHost;
      if (settled.has(id)) { paint(host, settled.get(id)); continue; }
      try { paint(host, await get(`/v2/credential-requests/${encodeURIComponent(id)}`)); }
      catch { host.innerHTML = '<div class="cc-done">This card is not yours to see.</div>'; }
    }
  }

  window.credentialCards = {mount, problem};
})();
