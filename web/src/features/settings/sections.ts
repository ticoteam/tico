/* Account holds what the account menu opens on: you, this device's look, your tokens. */
export const SECTION_IDS = [
  'general',
  'people',
  'agents',
  'computers',
  'providers',
  'tasks',
  'contacts',
  'account',
  'security',
  'billing',
  'history',
  'health',
  'changelog',
] as const;

export type SectionId = (typeof SECTION_IDS)[number];

/** null is the thin line between groups. */
export const NAV: readonly (readonly [SectionId, string] | null)[] = [
  ['general', 'General'],
  ['people', 'People'],
  ['agents', 'Agents'],
  ['computers', 'Computers'],
  ['providers', 'Providers'],
  ['tasks', 'Tasks'],
  ['contacts', 'Contacts'],
  null,
  ['account', 'Account'],
  ['security', 'Security'],
  ['billing', 'Billing'],
  null,
  ['history', 'History'],
  ['health', 'Health'],
  ['changelog', 'Changelog'],
];

export const isSection = (v: unknown): v is SectionId => SECTION_IDS.includes(v as SectionId);

export const sectionName = (id: SectionId): string => NAV.find((n) => n?.[0] === id)?.[1] ?? id;
