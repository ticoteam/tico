/* Frozen email previews are fetched through the viewer's own authority; all body representations stay inert. */
'use strict';

// The frozen email an approval shows: its send, and the bodies it was approved with, in order.
interface EmailSendPreview {
  preview_complete?: boolean;
  body?: string;
  bodies?: EmailSendPreviewBody[];
  send?: EmailSendPreviewSend;
}
interface EmailSendPreviewBody {
  type: string;
  text: string;
}
interface EmailSendPreviewSend {
  id: string;
  subject: string;
  mailbox?: string;
  recipients: string[];
  attachments?: EmailSendPreviewAttachment[];
  content_sha256: string;
}
interface EmailSendPreviewAttachment {
  part: number;
  name: string;
  size: number;
  sha256: string;
}

function emailSendPreviewHTML(data: EmailSendPreview | null | undefined): string {
  if (!data?.preview_complete || !data.send) return '<p class="err">The frozen email could not be loaded. Approval is unavailable.</p>';
  const send = data.send;
  // Who it goes to and from which mailbox, then what it says. The hashes the approval is bound to are not part of the
  // message, so they are not shown: Approve sends them (shown_hash) without the person reading them.
  const from = send.mailbox && send.mailbox.includes('@') ? `<p><span class="muted">From</span> ${esc(send.mailbox)}</p>` : '';
  return `<div class="email-send-preview"><strong>${esc(send.subject)}</strong><p><span class="muted">To</span> ${esc(send.recipients.join(', '))}</p>${from}
    ${(data.bodies || [{type: 'text/plain', text: data.body}]).map(body => `<section><span class="muted">${esc(body.type === 'text/html' ? 'HTML text' : 'Plain text')}</span><pre>${esc(body.text)}</pre></section>`).join('')}
    <ul>${(send.attachments || []).map(file => `<li><a href="/api/v2/email/sends/${encodeURIComponent(send.id)}/attachments/${Number(file.part)}" download>${esc(file.name)}</a> · ${esc(emailSize(file.size))}</li>`).join('')}</ul></div>`;
}
/** A size in words: 812 bytes, 14 KB, 2.3 MB. */
function emailSize(bytes: number): string {
  const n = Number(bytes) || 0;
  return n < 1024 ? `${n} bytes` : n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}
