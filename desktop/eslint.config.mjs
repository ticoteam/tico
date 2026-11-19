import js from '@eslint/js';
import { defineConfig } from 'eslint/config';
import tseslint from 'typescript-eslint';

const node = { process: 'readonly', console: 'readonly', setTimeout: 'readonly', clearTimeout: 'readonly', URL: 'readonly', fetch: 'readonly' };
const browser = { window: 'readonly', document: 'readonly' };

export default defineConfig(
  { ignores: ['dist/**', 'release/**', 'node_modules/**'] },
  js.configs.recommended,
  {
    files: ['src/**/*.ts'],
    extends: [tseslint.configs.recommended],
    rules: {
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/consistent-type-imports': 'error',
      // Pages never get Node, and web security stays on (platforms.md 4.3).
      'no-restricted-syntax': [
        'error',
        {
          selector: 'Property[key.name=/^(nodeIntegration|nodeIntegrationInSubFrames|nodeIntegrationInWorker|webviewTag|allowRunningInsecureContent)$/][value.value=true]',
          message: 'Pages never get Node, webviews or insecure content.',
        },
        {
          selector: 'Property[key.name=/^(contextIsolation|sandbox|webSecurity)$/][value.value=false]',
          message: 'contextIsolation, sandbox and webSecurity stay on.',
        },
      ],
    },
  },
  {
    files: ['src/preload.ts', 'src/localPreload.ts'],
    rules: {
      'no-restricted-imports': ['error', { patterns: [{ regex: '^(?!electron$)(?!\\./bridge$)', message: 'A sandboxed preload may import only electron' }] }],
    },
  },
  { files: ['src/local/**/*.js'], languageOptions: { sourceType: 'script', globals: browser } },
  { files: ['scripts/**/*.mjs', '*.mjs'], languageOptions: { globals: node } },
);
