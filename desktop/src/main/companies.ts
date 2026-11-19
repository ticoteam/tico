// The companies list, companies.json (platforms.md 4.2): which servers this app opens, which is on screen, and what
// is kept for each. Pure functions over the file's contents; main.ts does the reading and writing.
import { checkAddress, isAppUrl } from './address';

export type Company = {
  id: string;
  /** The server's origin. */
  url: string;
  partition: string;
  name: string;
  email: string;
  lastUrl?: string;
};

export type CompaniesFile = { version: 1; active: string | null; companies: Company[] };

export const COMPANY_ID = /^[a-z0-9_]{1,40}$/;
const DETAIL_LIMIT = 200;

export const emptyFile = (): CompaniesFile => ({ version: 1, active: null, companies: [] });

export const partitionFor = (id: string) => `persist:company-${id}`;

/** A fresh id from random hex (crypto.randomBytes in main). */
export const companyId = (hex: string) => `c${hex.toLowerCase().replace(/[^0-9a-f]/g, '').slice(0, 16)}`;

const text = (v: unknown) => (typeof v === 'string' ? v.slice(0, DETAIL_LIMIT) : '');

/** Reads companies.json's contents. Entries that are not valid are dropped; a repeated origin keeps the first. */
export function parseCompanies(json: unknown): CompaniesFile {
  const file = emptyFile();
  if (!json || typeof json !== 'object') return file;
  const o = json as Record<string, unknown>;
  if (o.version !== 1 || !Array.isArray(o.companies)) return file;
  const seen = new Set<string>();
  for (const raw of o.companies as unknown[]) {
    if (!raw || typeof raw !== 'object') continue;
    const c = raw as Record<string, unknown>;
    if (typeof c.id !== 'string' || !COMPANY_ID.test(c.id) || typeof c.url !== 'string') continue;
    const address = checkAddress(c.url);
    if (!address.ok || address.origin !== c.url || seen.has(c.url)) continue;
    if (file.companies.some((x) => x.id === c.id)) continue;
    seen.add(c.url);
    const company: Company = { id: c.id, url: c.url, partition: partitionFor(c.id), name: text(c.name), email: text(c.email) };
    if (typeof c.lastUrl === 'string' && isAppUrl(c.lastUrl, c.url)) company.lastUrl = c.lastUrl;
    file.companies.push(company);
  }
  file.active = file.companies.some((c) => c.id === o.active) ? (o.active as string) : (file.companies[0]?.id ?? null);
  return file;
}

/**
 * Today's Tauri orgs.json (`{version: 1, active, orgs: [{id, url, name, email, ...}]}`, app/src/orgs.rs), made into a
 * companies list once. Cookies live in WebKit and cannot move, so each company asks for sign-in once.
 */
export function migrateOrgs(json: unknown, newId: () => string): CompaniesFile | null {
  if (!json || typeof json !== 'object') return null;
  const o = json as Record<string, unknown>;
  if (!Array.isArray(o.orgs)) return null;
  let file = emptyFile();
  let active: string | null = null;
  for (const raw of o.orgs as unknown[]) {
    if (!raw || typeof raw !== 'object') continue;
    const org = raw as Record<string, unknown>;
    if (typeof org.url !== 'string') continue;
    const address = checkAddress(org.url);
    if (!address.ok) continue;
    const added = addCompany(file, address.origin, newId());
    file = added.file;
    file = reportSelf(file, added.company.id, { name: text(org.name), email: text(org.email) });
    if (org.id === o.active) active = added.company.id;
  }
  if (!file.companies.length) return null;
  return { ...file, active: active ?? file.companies[0]!.id };
}

export function findByOrigin(file: CompaniesFile, origin: string): Company | undefined {
  return file.companies.find((c) => c.url === origin);
}

/** Adds a server, or finds the company it already is. Either way it becomes the active one. */
export function addCompany(file: CompaniesFile, origin: string, id: string): { file: CompaniesFile; company: Company; existed: boolean } {
  const existing = findByOrigin(file, origin);
  if (existing) return { file: { ...file, active: existing.id }, company: existing, existed: true };
  if (!COMPANY_ID.test(id)) throw new Error('Bad company id');
  const company: Company = { id, url: origin, partition: partitionFor(id), name: '', email: '' };
  return { file: { ...file, active: id, companies: [...file.companies, company] }, company, existed: false };
}

export function removeCompany(file: CompaniesFile, id: string): CompaniesFile {
  const companies = file.companies.filter((c) => c.id !== id);
  const active = file.active === id ? (companies[0]?.id ?? null) : file.active;
  return { ...file, active, companies };
}

export function setActive(file: CompaniesFile, id: string): CompaniesFile {
  return file.companies.some((c) => c.id === id) ? { ...file, active: id } : file;
}

export function reportSelf(file: CompaniesFile, id: string, info: { name: string; email: string }): CompaniesFile {
  return {
    ...file,
    companies: file.companies.map((c) => (c.id === id ? { ...c, name: text(info.name), email: text(info.email) } : c)),
  };
}

export function setLastUrl(file: CompaniesFile, id: string, url: string): CompaniesFile {
  return {
    ...file,
    companies: file.companies.map((c) => (c.id === id && isAppUrl(url, c.url) ? { ...c, lastUrl: url } : c)),
  };
}
