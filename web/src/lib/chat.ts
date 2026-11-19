/**
 * A teammate's chat as the shell's `?chat=` names it (shell.openChat: `agent:<id>` or `person:<id>`). Actor ids
 * (`bot:<id>`, `human:<id>`) are not chat addresses.
 */
export function chatAddress(actor: string, kind?: 'person' | 'agent'): string {
  const at = actor.indexOf(':');
  const id = at < 0 ? actor : actor.slice(at + 1);
  const agent = kind ? kind === 'agent' : /^(bot|agent):/.test(actor);
  return `${agent ? 'agent' : 'person'}:${id}`;
}

/** The actor a chat address names (`bot:<id>` or `human:<id>`); none for a thread's own address or an unknown kind.
    An actor id put in the address by mistake still reads as that actor. */
export function chatActor(chat: string): string | undefined {
  const at = chat.indexOf(':');
  if (at < 1) return undefined;
  const kind = chat.slice(0, at);
  const id = chat.slice(at + 1);
  if (!id) return undefined;
  if (kind === 'agent' || kind === 'bot') return `bot:${id}`;
  if (kind === 'person' || kind === 'human') return `human:${id}`;
  return undefined;
}
