// A local server on the made-up Acme company (seeded.py), for the journeys: started fresh for each journey, so no run
// sees what another one did. It serves the app's build at /, as production does.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createInterface } from 'node:readline';

const ROOT = new URL('../../../', import.meta.url).pathname;
const SEEDED = new URL('./seeded.py', import.meta.url).pathname;

export interface Facts {
  mail: { send: string; approval: string; subject: string; mailbox: string };
  board: { pipeline: string; tasks: { id: string; number: number; title: string }[] };
  thread: string;
}
export interface Seeded {
  url: string;
  /** The local owner sign-in secret (Dana, the owner). */
  token: string;
  /** Each teammate's bearer, for the API from the test. */
  tokens: Record<string, string>;
  facts: Facts;
  stop: () => Promise<void>;
}

/** The repository's .venv Python (scripts/dev_setup.sh), else python3 on the PATH; TICO_PYTHON overrides. */
const python = () => process.env.TICO_PYTHON || (existsSync(`${ROOT}.venv/bin/python`) ? `${ROOT}.venv/bin/python` : 'python3');

export function startSeeded(): Promise<Seeded> {
  const child = spawn(python(), [SEEDED, '--web-dir', process.env.TICO_WEB_DIR || `${ROOT}build/web`], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let err = '';
  child.stderr.on('data', (d: Buffer) => {
    err = (err + String(d)).slice(-4000);
  });
  const exited = new Promise<void>((done) => child.once('exit', () => done()));
  const stop = async () => {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
    await exited;
  };
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      void stop();
      reject(new Error(`seeded.py did not answer in 120 s:\n${err}`));
    }, 120_000);
    child.once('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`seeded.py exited (${code}) before it answered:\n${err}`));
    });
    createInterface({ input: child.stdout }).on('line', (line) => {
      if (!line.startsWith('HUB ')) return;
      clearTimeout(timer);
      resolve({ ...(JSON.parse(line.slice(4)) as Omit<Seeded, 'stop'>), stop });
    });
  });
}
