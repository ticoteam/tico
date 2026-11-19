// Lint rules, including the import boundaries from architecture.md 2. A folder imports another folder only through
// what that folder exports; anything else is a lint error, not a review comment.
import js from '@eslint/js';
import { defineConfig } from 'eslint/config';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

// Only these files talk to the server or build queries; everything else asks them.
const SERVER_FILES = ['src/api/**', 'src/data/**', 'src/live/**', 'src/features/*/queries.ts'];

// A later flat-config block replaces a rule entirely, so each folder's list repeats the shared part.
const SHARED = [{ regex: '^\\.\\./', message: 'Import through the @/ alias, not ../' }];
const ban = (...rules) => ({
  'no-restricted-imports': ['error', { patterns: [...SHARED, ...rules.map(([regex, message]) => ({ regex, message }))] }],
});

const FEATURE_INTERNALS = ['^@/features/[^/]+/(?!exports$)', 'Use a feature only through its exports.ts'];
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
  { files: ['scripts/**', 'vite.config.ts', '*.config.js', 'e2e/**', 'parity/**'], languageOptions: { globals: globals.node } },
  { files: ['public/boot.js'], languageOptions: { sourceType: 'script' } },

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
    rules: ban(FEATURE_INTERNALS, NO('api', 'Routes load through data/ or a feature'), NO('live', 'Routes do not open the stream'), NO('actions', 'Routes do not run actions')),
  },
  {
    files: ['src/features/**'],
    rules: ban(
      FEATURE_INTERNALS,
      ['^@/live/(?!follow$)', 'Features use live only through follow'],
      NO('shell', 'Features do not reach into the shell'),
      ['^@/actions/(?!run$|types$)', 'Features use actions only through run and types'],
      NO('tico', 'Features do not reach into Tico'),
    ),
  },
  {
    files: ['src/kit/**'],
    rules: ban(
      ...['data', 'api', 'actions', 'features', 'live', 'shell', 'tico', 'routes'].map((a) => NO(a, 'The kit draws; it knows no data or app')),
    ),
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
    rules: ban(...['features', 'kit', 'shell', 'routes', 'tico'].map((a) => NO(a, 'Actions are below the screens'))),
  },
  {
    files: ['src/lib/**'],
    rules: ban(['^@/(?!lib/)', 'lib/ imports only lib/'], ['^react(-dom)?(/|$)', 'lib/ is plain functions, no React']),
  },
);
