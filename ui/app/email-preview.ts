/* Frozen email previews are fetched through the viewer's own authority; all body representations stay inert. */
'use strict';

function emailSendPreviewHTML(data) {
  if (!data?.preview_complete || !data.send) return '<p class="err">The frozen email could not be loaded. Approval is unavailable.</p>';
  const send = data.send;
  return `<div class="email-send-preview"><strong>${esc(send.subject)}</strong><p>${esc(send.recipients.join(', '))}</p>
    ${(data.bodies || [{type: 'text/plain', text: data.body}]).map(body => `<section><span class="muted">${esc(body.type === 'text/html' ? 'HTML text' : 'Plain text')}</span><pre>${esc(body.text)}</pre></section>`).join('')}
    <ul>${(send.attachments || []).map(file => `<li><a href="/api/v2/email/sends/${encodeURIComponent(send.id)}/attachments/${Number(file.part)}" download>${esc(file.name)}</a> · ${esc(file.size)} bytes <code>${esc(file.sha256)}</code></li>`).join('')}</ul>
    <code>${esc(send.content_sha256)}</code></div>`;
}
