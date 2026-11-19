// Playwright helper: a local server on a real company's rehearsal copy (serve.py), and a page signed in to it.
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import type { Browser, Page } from '@playwright/test';

export type Company = 'tico-team' | 'tidy';
export type Hub = { company: Company; url: string; pid: number; token: string; db: string; stop: () => Promise<void> };

const SERVE = new URL('./serve.py', import.meta.url).pathname;

/** Starts serve.py (making the copy first if there is none) and resolves once the server answers. */
export function startHub({ company, port = 8765, refresh = false }: { company: Company; port?: number; refresh?: boolean }): Promise<Hub> {
  const args = [SERVE, '--company', company, '--port', String(port), ...(refresh ? ['--refresh'] : [])];
  const child = spawn('python3', args, { stdio: ['ignore', 'pipe', 'inherit'] });
  const exited = new Promise<void>((done) => child.once('exit', () => done()));
  const stop = async () => {
    if (child.exitCode === null) child.kill('SIGTERM');
    await exited;
  };
  return new Promise((resolve, reject) => {
    child.once('exit', (code) => reject(new Error(`serve.py exited (${code}) before the server answered`)));
    createInterface({ input: child.stdout }).on('line', (line) => {
      if (!line.startsWith('HUB ')) return void process.stdout.write(`[hub] ${line}\n`);
      const hub = JSON.parse(line.slice(4));
      const token = readFileSync(hub.token_file, 'utf8').trim();
      resolve({ company, url: hub.url, pid: hub.pid, token, db: hub.db, stop });
    });
  });
}

/** A new page signed in as the copy's owner (the only identity a copy has), opened at `at` (default: /next/). */
export async function pageAs(browser: Browser, hub: Hub, { phone = false, at }: { phone?: boolean; at?: string } = {}): Promise<Page> {
  const context = await browser.newContext(
    phone
      ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 }
      : { viewport: { width: 1440, height: 900 } },
  );
  const page = await context.newPage();
  // The cookie ignores the port, so this also signs in a Vite page at 127.0.0.1:5173. Land on /healthz, not the old app.
  await page.goto(`${hub.url}/api/v2/local-signin?token=${encodeURIComponent(hub.token)}&next=/healthz`);
  await page.goto(at ?? `${hub.url}/next/`);
  return page;
}
