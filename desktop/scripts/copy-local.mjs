// Copies the two local pages (plain HTML, CSS and JS) next to the compiled main process.
import { cpSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
cpSync(join(root, 'src/local'), join(root, 'dist/local'), { recursive: true });
