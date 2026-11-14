/* ui/app/secret-drop.ts — The Tools chat's key detector, in the page (spec tools-chat-key-drop). A paste, a dropped text
   or typed words in the Tools owner's composer are scanned here, with no network call and no model, before any of it
   reaches the text box; each secret becomes a locked item and only its name is ever sent.
   The rules are backend/secret_rules.json, the one file the server's backstop (backend/secret_drop.py) reads too; the
   page fetches it once from GET /api/v2/secret-rules (sdRules). backend/tests/fixtures/secret_drop_cases.json is run by
   both test suites (ui/tests/secret-drop-unit.cts here), so the two detectors agree on every case. Keep this file and
   secret_drop.py step for step alike: offsets here count UTF-16 units, there code points, so the tests compare values.
   Nothing here logs, stores or draws a value.
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them. */
'use strict';

interface SdRule {
  id: string; re: string; flags?: string; group: number[]; kind: string; type: string;
  name?: string; base?: string; service?: string; user?: number; name_group?: number; service_group?: number; check?: string;
  word_group?: number; spaced?: boolean;
}
interface SdWord { re: string; kind: string; base: string; type: string; strong: boolean; spaced?: boolean }
interface SdRules {
  version: number; zero_width: string; services: {re: string; service: string}[]; certain: SdRule[]; words: SdWord[];
  placeholder: string; negative: string[]; negative_keyed: string[]; negative_password: string[]; token: string; db_schemes: string;
  suspect: {entropy_length: number; entropy_bits: number; mixed_length: number; plain_hex: number};
}
/** One secret found: what it is and where, and its value, which lives only in page memory. */
interface SdItem {
  tier: 'certain' | 'suspect'; rule: string; kind: string; service: string; name: string; type: string;
  username: string; start: number; end: number; value: string;
}
interface SdFound extends SdItem { structured: boolean }

const SD_CACHE = new Map<string, RegExp>();
function sdRe(pattern: string, flags = ''): RegExp {
  const key = flags + '\u0000' + pattern;
  let re = SD_CACHE.get(key);
  if (!re) { re = new RegExp(pattern, flags.replace(/[^im]/g, '') + 'gd'); SD_CACHE.set(key, re); }
  re.lastIndex = 0;
  return re;
}
const sdTest = (pattern: string, text: string, flags = ''): boolean => { const re = sdRe(pattern, flags); const hit = re.test(text); re.lastIndex = 0; return hit; };
const sdFull = (pattern: string, text: string, flags = ''): boolean => sdTest('^(?:' + pattern + ')$', text, flags);
const sdAll = (pattern: string, text: string, flags = ''): RegExpExecArray[] => Array.from(text.matchAll(sdRe(pattern, flags)));

function sdNormalize(rules: SdRules, text: string): string {
  return (text || '').normalize('NFKC').replace(sdRe(rules.zero_width), '');
}
const sdPlaceholder = (rules: SdRules, value: string): boolean => sdTest(rules.placeholder, value, 'i');
type SdShapes = 'negative' | 'negative_keyed' | 'negative_password';
/** Wholly one of the ordinary shapes in the rule list `shapes` (secret_drop._negative_full). */
const sdNegativeFull = (rules: SdRules, value: string, shapes: SdShapes = 'negative'): boolean =>
  rules[shapes].some(p => sdFull(p, value));
// Character classes are ASCII on both sides: a letter outside ASCII counts as a symbol in Python and JavaScript.
const sdHas = (text: string, cls: string): boolean => sdTest(cls, text);

/** A value that reads as a key, not a word (secret_drop.secretish). */
function sdSecretish(rules: SdRules, value: string, shapes: SdShapes = 'negative'): boolean {
  if (value.length < 6 || sdPlaceholder(rules, value) || sdNegativeFull(rules, value, shapes)) return false;
  const digit = sdHas(value, '[0-9]'), symbol = sdHas(value, '[^A-Za-z0-9]');
  const mixed = sdHas(value.slice(1), '[A-Z]') && sdHas(value, '[a-z]');
  return digit || symbol || (mixed && value.length >= 10);
}
function sdWord(rules: SdRules, name: string): SdWord | null {
  const lowered = name.toLowerCase();
  return rules.words.find(w => sdTest(w.re, lowered)) || null;
}
/** Which ordinary shapes still cancel a value after the context word `name` (secret_drop._shapes). */
function sdShapes(rules: SdRules, rule: SdRule, name: string): SdShapes {
  const word = name ? sdWord(rules, name) : null;
  if (!word?.strong || (rule.spaced && word.spaced === false)) return 'negative';
  return word.type === 'password' ? 'negative_password' : 'negative_keyed';
}
const sdWordOnly = (rules: SdRules, named: string): boolean => !!sdWord(rules, named)?.base;
function sdServiceNear(rules: SdRules, text: string, start: number): string {
  let window = text.slice(Math.max(0, start - 80), start).toLowerCase();
  window = window.slice(window.lastIndexOf('\n') + 1);
  let best = '', at = -1;
  for (const svc of rules.services) {
    for (const m of sdAll('(?<![a-z0-9])(?:' + svc.re + ')(?![a-z0-9])', window)) {
      if (m.index > at) { best = svc.service; at = m.index; }
    }
  }
  return best;
}
function sdServiceIn(rules: SdRules, name: string): string {
  const words = name.toLowerCase().replace(sdRe('[^a-z0-9]+'), ' ');
  return sdServiceNear(rules, ' ' + words + ' ', words.length + 2);
}
const sdServiceWord = (rules: SdRules, word: string): string =>
  rules.services.find(s => sdFull(s.re, word.toLowerCase()))?.service || '';
function sdEnvName(text: string): string {
  return text.toUpperCase().replace(sdRe('[^A-Z0-9_]'), '_').replace(sdRe('_+'), '_').replace(/^_+|_+$/g, '');
}
function sdPrefixed(service: string, base: string): string {
  const head = sdEnvName(service) + '_';
  return service && !base.startsWith(head) ? head + base : base;
}

function sdCertain(rules: SdRules, text: string, rule: SdRule): SdFound[] {
  const found: SdFound[] = [];
  for (const m of sdAll(rule.re, text, rule.flags || '')) {
    const spans = m.indices || [];
    const group = rule.group.find(g => spans[g] !== undefined) ?? -1;
    const span = group >= 0 ? spans[group] : undefined;
    if (!span || span[1] <= span[0]) continue;
    const value = text.slice(span[0], span[1]);
    const check = rule.check || '';
    let kind = rule.kind, base = rule.base || '', type = rule.type;
    const named = rule.name_group ? m[rule.name_group] || '' : '';
    const shapes = sdShapes(rules, rule, rule.word_group ? m[rule.word_group] || '' : named);
    if (check === 'not_placeholder' && sdPlaceholder(rules, value)) continue;
    if (check === 'secretish' && !sdSecretish(rules, value, shapes)) continue;
    if (check === 'env' || check === 'context') {
      const word = sdWord(rules, named);
      if (shapes !== 'negative' && (sdPlaceholder(rules, value) || sdNegativeFull(rules, value, shapes))) continue;
      if (shapes === 'negative' && !sdSecretish(rules, value)) continue;
      if (word) { kind = word.kind; type = word.type; base = word.base || base; }
    } else if (check === 'secretish' && named) {
      const word = sdWord(rules, named);
      if (word) { kind = word.kind; type = word.type; base = word.base || base; }
    }
    let service = rule.service || '';
    if (rule.service_group) service = sdServiceWord(rules, m[rule.service_group] || '');
    const username = rule.user ? m[rule.user] || '' : '';
    if (!service && named) service = sdServiceIn(rules, named);
    if (!service && (!named || sdWordOnly(rules, named))) service = sdServiceNear(rules, text, m.index);
    if (rule.id === 'url_login') {
      const scheme = text.slice(m.index, text.indexOf('://', m.index));
      if (sdTest(rules.db_schemes, scheme, 'i')) service = 'Database';
    }
    let name: string;
    if (rule.name) name = rule.name;
    else if (check === 'env' || (check === 'context' && named && !sdWordOnly(rules, named))) name = sdEnvName(named);
    else name = sdPrefixed(service, base);
    if (kind === 'Login') kind = service ? service + ' login' : 'Login';
    found.push({tier: 'certain', rule: rule.id, kind, service, name, type, username, start: span[0], end: span[1], value,
      structured: (check === 'env' || check === 'context') && !!named});
  }
  return found;
}

function sdEntropy(token: string): number {
  const counts = new Map<string, number>();
  for (const ch of token) counts.set(ch, (counts.get(ch) || 0) + 1);
  const n = token.length;
  let sum = 0;
  for (const c of counts.values()) sum -= (c / n) * Math.log2(c / n);
  return sum;
}
const sdPlain = (part: string, plainHex: number): boolean =>
  sdFull('[A-Za-z]+', part) || sdFull('[0-9]+', part) || part.length <= 3 || (part.length <= plainHex && sdFull('[0-9a-fA-F]+', part));
function sdSuspect(rules: SdRules, token: string): boolean {
  const cfg = rules.suspect;
  const parts = token.split(/[-_./:]/).filter(Boolean);
  if (parts.every(p => sdPlain(p, cfg.plain_hex)) || sdPlaceholder(rules, token)) return false;
  const digit = sdHas(token, '[0-9]'), symbol = sdHas(token, '[^A-Za-z0-9]');
  const lower = sdHas(token, '[a-z]'), upper = sdHas(token, '[A-Z]');
  if (token.length >= cfg.entropy_length && sdEntropy(token) >= cfg.entropy_bits && (digit || symbol)) return true;
  return token.length >= cfg.mixed_length && digit && (Number(lower) + Number(upper) + Number(digit) + Number(symbol)) >= 3;
}
function sdSuspects(rules: SdRules, text: string, taken: SdFound[]): SdFound[] {
  let flat = text;
  for (const item of taken) flat = flat.slice(0, item.start) + ' '.repeat(item.end - item.start) + flat.slice(item.end);
  for (const pattern of rules.negative) flat = flat.replace(sdRe(pattern), s => ' '.repeat(s.length));
  const found: SdFound[] = [];
  for (const m of sdAll(rules.token, flat)) {
    const token = m[0].replace(/[.,:;!?)]+$/, '').replace(/^\(+/, '');
    const lead = m[0].indexOf(token);
    if (!token || !sdSuspect(rules, token)) continue;
    const start = m.index + lead, service = sdServiceNear(rules, text, start);
    found.push({tier: 'suspect', rule: 'suspect', kind: 'Looks like a key', service,
      name: service ? sdPrefixed(service, 'API_KEY') : 'SECRET', type: 'api_key', username: '',
      start, end: start + token.length, value: text.slice(start, start + token.length), structured: false});
  }
  return found;
}

/** Each secret in `text` (normalized first), in text order: secret_drop.scan, step for step. */
function sdScan(rules: SdRules, text: string): SdItem[] {
  const t = sdNormalize(rules, text);
  const taken: SdFound[] = [];
  for (const rule of rules.certain) {
    for (const item of sdCertain(rules, t, rule)) {
      const other = taken.find(o => item.start < o.end && o.start < item.end);
      if (!other) taken.push(item);
      else if (item.structured && other.start === item.start && other.end === item.end) other.name = item.name;
    }
  }
  taken.push(...sdSuspects(rules, t, taken));
  taken.sort((a, b) => a.start - b.start);
  const seen = new Map<string, number>();
  return taken.map(({structured: _structured, ...item}) => {
    const n = (seen.get(item.name) || 0) + 1;
    seen.set(item.name, n);
    return n > 1 ? {...item, name: item.name + '_' + n} : item;
  });
}

/** The vault's preview rule (backend/credentials.py): first 3 + last 3 of a key or token of 12+ characters, else dots. */
function sdPreview(item: Pick<SdItem, 'type' | 'value'>): string {
  return (item.type === 'api_key' || item.type === 'token') && item.value.length >= 12
    ? item.value.slice(0, 3) + '…' + item.value.slice(-3) : '••••••';
}

/** The digest the server's backstop lets through for a suspect the person un-marked (secret_drop.digest). */
async function sdDigest(rules: SdRules, value: string): Promise<string> {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(sdNormalize(rules, value)));
  return Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, '0')).join('');
}

let SD_RULES: Promise<SdRules | null> | null = null;
/** The rule file, fetched once per page load; null when it can't be read, and the composer then refuses a paste. */
function sdRules(): Promise<SdRules | null> {
  SD_RULES ||= get<SdRules>('/v2/secret-rules').then(r => (r && Array.isArray(r.certain) ? r : null)).catch(() => { SD_RULES = null; return null; });
  return SD_RULES;
}
