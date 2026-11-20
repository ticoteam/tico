// Lint rules, including the import boundaries from architecture.md 2. A folder imports another folder only through
// what that folder exports; anything else is a lint error, not a review comment.
import js from '@eslint/js';
import { defineConfig } from 'eslint/config';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

// Only these files talk to the server or build queries; everything else asks them.
const SERVER_FILES = ['src/api/**', 'src/data/**', 'src/live/**', 'src/*/queries.ts', 'src/features/*/queries.ts'];

// A later flat-config block replaces a rule entirely, so each folder's list repeats the shared part.
const SHARED = [
  { regex: '^\\.\\./', message: 'Import through the @/ alias, not ../' },
  // routing.ts: what loads before a page's code (address checks, first reads, the shell's counts), never a component;
  // exports.ts: the rest. The first load carries only the routing files, so a page's code comes with its route.
  { regex: '^@/features/[^/]+/(?!exports$|routing$)', message: 'Use a feature only through its exports.ts or routing.ts' },
];
const ban = (...rules) => ({
  'no-restricted-imports': ['error', { patterns: [...SHARED, ...rules.map(([regex, message]) => ({ regex, message }))] }],
});

const NO = (area, why) => [`^@/${area}(/|$)`, why];

export default defineConfig(
  { ignores: ['node_modules/', 'dist/', '.tanstack/', 'src/routeTree.gen.ts', 'src/api/schema.d.ts'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  {
    languageOptions: { globals: globals.browser },
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      '@typescript-eslint/consistent-type-imports': 'error',
      ...ban(),
    },
  },
  { files: ['scripts/**', 'vite.config.ts', '*.config.js', 'e2e/**'], languageOptions: { globals: globals.node } },
  { files: ['public/boot.js'], languageOptions: { sourceType: 'script' } },
  { files: ['public/sw.js'], languageOptions: { sourceType: 'script', globals: globals.serviceworker } },

  // No fetch and no inline query outside the server files.
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: SERVER_FILES,
    rules: {
      'no-restricted-globals': ['error', { name: 'fetch', message: 'Call the server from src/api or a feature’s queries.ts' }],
      'no-restricted-properties': [
        'error',
        { object: 'window', property: 'fetch', message: 'Call the server from src/api or a feature’s queries.ts' },
        { object: 'globalThis', property: 'fetch', message: 'Call the server from src/api or a feature’s queries.ts' },
      ],
      'no-restricted-syntax': [
        'error',
        {
          selector:
            'CallExpression[callee.name=/^use(Suspense)?(Infinite)?Query$/] > ObjectExpression.arguments:first-child > Property[key.name=/^query(Key|Fn)$/]',
          message: 'Build query options in data/ or the feature’s queries.ts and pass them in',
        },
      ],
    },
  },

  // The boundaries table.
  {
    files: ['src/routes/**'],
    // the root route is the shell's wiring: it starts the stream and mounts the action layer
    ignores: ['src/routes/__root.tsx'],
    rules: ban(NO('api', 'Routes load through data/ or a feature'), NO('live', 'Routes do not open the stream'), NO('actions', 'Routes do not run actions')),
  },
  {
    files: ['src/features/**'],
    rules: ban(
      ['^@/live(?!/follow$)', 'Features use live only through @/live/follow'],
      NO('shell', 'Features do not reach into the shell'),
      ['^@/actions/(?!run$|types$)', 'Features use actions only through run and types'],
      ['^@/tico(?!/dot$)(/|$)', 'Features reach Tico only through @/tico/dot'],
    ),
  },
  {
    files: ['src/kit/**'],
    rules: ban(
      ...['data', 'api', 'actions', 'features', 'live', 'shell', 'tico', 'routes'].map((a) => NO(a, 'The kit draws; it knows no data or app')),
      ['^@tanstack/react-router(/|$)', 'The kit links through kit/Link; main.tsx gives it the router'],
    ),
  },
  {
    // The screen parts every page shares: kit, data, live, actions and motion below them, never a route or a feature.
    files: ['src/conversation/**', 'src/search/**', 'src/pagekinds/**'],
    rules: ban(NO('routes', 'Routes sit above the screen parts')),
  },
  {
    files: ['src/data/**'],
    rules: ban(...['features', 'kit', 'shell', 'tico', 'actions', 'routes', 'live'].map((a) => NO(a, 'data/ is below the screens and the stream'))),
  },
  {
    files: ['src/live/**'],
    rules: ban(...['features', 'kit', 'shell', 'tico', 'actions', 'routes'].map((a) => NO(a, 'live/ is below the screens'))),
  },
  {
    files: ['src/actions/**'],
    // Actions fold sheets (@/shell) and fly from the Dot (@/tico/dot); they never draw.
    rules: ban(...['features', 'kit', 'routes'].map((a) => NO(a, 'Actions are below the screens'))),
  },
  {
    files: ['src/lib/**'],
    rules: ban(['^@/(?!lib/)', 'lib/ imports only lib/'], ['^react(-dom)?(/|$)', 'lib/ is plain functions, no React']),
  },
);
