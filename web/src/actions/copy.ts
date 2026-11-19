// Every string a person reads from the base actions: titles (search rows, the catalog) and did lines.
export const goTo = 'Go to a page';
export const didGoTo = (name: string) => `Opened ${name}`;
export const openChat = 'Open a second chat';
export const didOpenChat = 'Opened the chat';
export const closeChat = 'Close the second chat';
export const didCloseChat = 'Closed the chat';
export const fold = 'Fold or open a sheet';
export const didFold = (name: string, folded: boolean | undefined) =>
  folded === false ? `Opened ${name}` : folded ? `Folded ${name}` : `Folded or opened ${name}`;
export const openTask = 'Open a task';
export const didOpenTask = (n: number) => `Opened #${n}`;
export const filter = 'Filter the page';
export const didFilter = 'Filtered the page';
export const search = 'Search';
export const didSearch = (q: string | undefined) => (q ? `Searched for ${q}` : 'Opened Search');

export const sheetName: Record<string, string> = {
  tico: 'Tico',
  menu: 'the menu',
  list: 'the list',
  widgets: 'the widgets',
  side: 'the side',
};

/** The menu's pages, for search rows and did lines: [path, name]. */
export const pages: ReadonlyArray<readonly [string, string]> = [
  ['/tasks', 'Tasks'],
  ['/calendar', 'Calendar'],
  ['/threads', 'Threads'],
  ['/updates', 'Updates'],
  ['/company', 'Company'],
  ...['Strategy', 'Finance', 'Growth', 'Products', 'Risk', 'Routines', 'Listening', 'Rules', 'Usage'].map(
    (k) => [`/company/${k.toLowerCase()}`, `Company, ${k}`] as const,
  ),
  ['/library', 'Library'],
  ...['Files', 'Docs', 'Tools', 'Skills', 'Contacts'].map((k) => [`/library/${k.toLowerCase()}`, `Library, ${k}`] as const),
  ['/settings', 'Settings'],
];
