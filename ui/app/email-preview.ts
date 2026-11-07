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
  return `<div class="email-send-preview"><strong>${esc(send.subject)}</strong><p>${esc(send.recipients.join(', '))}</p>
    ${(data.bodies || [{type: 'text/plain', text: data.body}]).map(body => `<section><span class="muted">${esc(body.type === 'text/html' ? 'HTML text' : 'Plain text')}</span><pre>${esc(body.text)}</pre></section>`).join('')}
    <ul>${(send.attachments || []).map(file => `<li><a href="/api/v2/email/sends/${encodeURIComponent(send.id)}/attachments/${Number(file.part)}" download>${esc(file.name)}</a> · ${esc(file.size)} bytes <code>${esc(file.sha256)}</code></li>`).join('')}</ul>
    <code>${esc(send.content_sha256)}</code></div>`;
}
