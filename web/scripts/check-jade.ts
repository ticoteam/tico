// The Dot's jade and Late's amber may appear only in their own files; the removed red appears nowhere.
import { globSync, readFileSync } from 'node:fs';

const RULES: { re: RegExp; ok: RegExp; what: string }[] = [
  { re: /var\(--(dot|dot-hi|dot-lo|on-dot|dot-shadow)\)/, ok: /^src\/(styles\/dot\.css|tico\/dot\/|motion\/)/, what: 'jade (the Dot, its droplets, the Flow mark)' },
  { re: /var\(--warn-(ink|line)\)/, ok: /^src\/styles\/late\.css$/, what: 'amber (a task row’s Late pill)' },
  { re: /\bpill-warn\b/, ok: /^src\/(styles\/late\.css$|.*\/LatePill\.tsx$)/, what: 'the Late pill' },
  { re: /var\(--danger-/, ok: /^$/, what: 'red (removed)' },
];

let bad = 0;
for (const file of globSync('src/**/*.{css,ts,tsx}')) {
  if (file.startsWith('src/styles/tokens') || file.endsWith('.gen.ts') || file.endsWith('.d.ts')) continue;
  readFileSync(file, 'utf8')
    .split('\n')
    .forEach((line, i) => {
      for (const r of RULES) {
        if (r.re.test(line) && !r.ok.test(file)) {
          bad++;
          console.log(`${file}:${i + 1}: ${r.what} is allowed only in its own file`);
        }
      }
    });
}
process.exit(bad ? 1 : 0);
