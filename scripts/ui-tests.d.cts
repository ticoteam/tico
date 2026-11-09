// Types for scripts/ui-tests.cjs as the test file uses them. Type-only: emits nothing.
export interface ProcRow {
  pid: number;
  ppid: number;
  start: string;
  command: string;
}
export interface StoppedProcess {
  pid: number;
  command: string;
}
export interface StoppedList extends Array<StoppedProcess> {
  errors: string[];
}
export interface RunResult {
  code: number | null;
  timedOut: boolean;
  secs: number;
  out: string;
  pending: string[];
  calls: string[];
  stopped: StoppedList;
}
export interface CpuTimes {
  times: {user: number; nice: number; sys: number; idle: number; irq: number};
}
export function resources(env: Record<string, string | undefined>, cores: number, idleFraction: number): {jobs: number; slowdown: number; timeout: number};
export function sampleCpu(readCpus: () => CpuTimes[] | undefined, sleep: (ms: number) => Promise<void>): Promise<{cores: number; idleFraction: number}>;
export function runScripts(files: string[], jobs: number, run: (file: string) => Promise<void>): Promise<void>;
export function runScript(file: string, options?: {env?: Record<string, string | undefined>; timeout?: number; grace?: number; preload?: string; stop?: (pid: number) => StoppedProcess[]}): Promise<RunResult>;
export function stopTree(pid: number, deps?: {list(): ProcRow[]; signal(pid: number, name: string): void}): StoppedList;
export function stoppedReport(result: {pending: string[]; calls: string[]; stopped: StoppedList}): string;
export function typeScriptGuard(files: string[], version?: string): string | null;
