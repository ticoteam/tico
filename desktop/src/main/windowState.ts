// The main window's remembered frame. Pure, apart from the two small file helpers at the end, which take the
// file functions they use so tests need no disk. Frames are in screen points (DIPs), as Electron reports them.
import type * as fs from 'node:fs';

export type Frame = { x: number; y: number; width: number; height: number };
export type WindowState = Frame & { maximized: boolean };

export const MIN_WIDTH = 360; // today's MIN_WIDTH/HEIGHT (app/src/main.rs)
export const MIN_HEIGHT = 400;
const DEFAULT_WIDTH = 1240;
const DEFAULT_HEIGHT = 820;
/** The band along the window's top that must stay on a display so the window can be grabbed. */
const GRAB_BAND = 24;

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

export function parseWindowState(json: unknown): WindowState | null {
  if (!json || typeof json !== 'object') return null;
  const o = json as Record<string, unknown>;
  const { x, y, width, height } = o;
  if (!finite(x) || !finite(y) || !finite(width) || !finite(height) || width <= 0 || height <= 0) return null;
  return {
    x: Math.round(x),
    y: Math.round(y),
    width: Math.round(Math.max(MIN_WIDTH, width)),
    height: Math.round(Math.max(MIN_HEIGHT, height)),
    maximized: o.maximized === true,
  };
}

/** Whether the window's top band is on some display, at least 80 wide (or the whole window, if narrower). */
export function reachable(frame: Frame, areas: readonly Frame[]): boolean {
  return areas.some((a) => {
    const left = Math.max(frame.x, a.x);
    const right = Math.min(frame.x + frame.width, a.x + a.width);
    const top = Math.max(frame.y, a.y);
    const bottom = Math.min(frame.y + GRAB_BAND, a.y + a.height);
    return right - left >= Math.min(80, frame.width) && bottom > top;
  });
}

/** 1240 by 820 where that fits, centered in the work area, never larger than it. */
export function fallbackFrame(area: Frame): Frame {
  const width = Math.max(1, Math.min(DEFAULT_WIDTH, area.width));
  const height = Math.max(1, Math.min(DEFAULT_HEIGHT, area.height));
  return {
    x: Math.round(area.x + (area.width - width) / 2),
    y: Math.round(area.y + (area.height - height) / 2),
    width,
    height,
  };
}

/** The frame to open with: the saved one while it can still be grabbed (a display may have gone), else the default. */
export function restoreFrame(saved: WindowState | null, areas: readonly Frame[], primary: Frame): WindowState {
  if (saved && reachable(saved, areas)) return saved;
  return { ...fallbackFrame(primary), maximized: saved?.maximized ?? false };
}

export function loadWindowState(file: string, read: typeof fs.readFileSync): WindowState | null {
  try {
    return parseWindowState(JSON.parse(read(file, 'utf8')));
  } catch {
    return null;
  }
}

export function saveWindowState(
  file: string,
  state: WindowState,
  io: { writeFileSync: typeof fs.writeFileSync; renameSync: typeof fs.renameSync },
): void {
  const tmp = `${file}.tmp`;
  io.writeFileSync(tmp, JSON.stringify(state));
  io.renameSync(tmp, file);
}
