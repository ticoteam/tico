// The app's log for Tico support: one file that rolls over at 1 MB, keeping one older file. Lines name origins and
// events only, never full URLs (they can carry sign-in codes) or anything a page sent.
import * as fs from 'node:fs';
import * as path from 'node:path';

const LIMIT = 1024 * 1024;
let file: string | null = null;

export function openLog(dir: string): string {
  fs.mkdirSync(dir, { recursive: true });
  file = path.join(dir, 'tico.log');
  return file;
}

export function logPath(): string | null {
  return file;
}

export function log(level: 'info' | 'warn' | 'error', message: string): void {
  const line = `${new Date().toISOString()} ${level} ${message.replace(/\s+/g, ' ').slice(0, 500)}\n`;
  if (!file) return;
  try {
    if (fs.existsSync(file) && fs.statSync(file).size > LIMIT) fs.renameSync(file, `${file}.1`);
    fs.appendFileSync(file, line);
  } catch {
    // A log that cannot be written must never stop the app.
  }
}

export function logTail(lines = 200): string[] {
  if (!file) return [];
  try {
    return fs.readFileSync(file, 'utf8').trimEnd().split('\n').slice(-lines);
  } catch {
    return [];
  }
}
