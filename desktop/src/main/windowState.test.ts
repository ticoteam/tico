import { describe, expect, it } from 'vitest';
import { fallbackFrame, loadWindowState, parseWindowState, reachable, restoreFrame, saveWindowState } from './windowState';

const laptop = { x: 0, y: 25, width: 1512, height: 920 };
const external = { x: 1512, y: -200, width: 2560, height: 1415 };

describe('window state', () => {
  it('reads a saved frame, keeping the minimum size, and refuses junk', () => {
    expect(parseWindowState({ x: 10.4, y: 30, width: 200, height: 300, maximized: true })).toEqual({ x: 10, y: 30, width: 360, height: 400, maximized: true });
    expect(parseWindowState({ x: 'a', y: 0, width: 800, height: 600 })).toBeNull();
    expect(parseWindowState({ x: 0, y: 0, width: Number.NaN, height: 600 })).toBeNull();
    expect(parseWindowState(null)).toBeNull();
  });

  it('keeps a saved frame only where its top can still be grabbed', () => {
    expect(reachable({ x: 100, y: 100, width: 1240, height: 820 }, [laptop])).toBe(true);
    expect(reachable({ x: 1700, y: 0, width: 1240, height: 820 }, [laptop, external])).toBe(true);
    // The external display was unplugged.
    expect(reachable({ x: 1700, y: 0, width: 1240, height: 820 }, [laptop])).toBe(false);
    expect(reachable({ x: 100, y: -500, width: 1000, height: 820 }, [laptop])).toBe(false);
    expect(reachable({ x: 1480, y: 100, width: 1240, height: 820 }, [laptop])).toBe(false);
    expect(reachable({ x: 100, y: 100, width: 1240, height: 820 }, [])).toBe(false);
  });

  it('falls back to 1240 by 820 centered, fitted to a small display', () => {
    expect(fallbackFrame({ x: 0, y: 25, width: 2560, height: 1415 })).toEqual({ x: 660, y: 323, width: 1240, height: 820 });
    const small = fallbackFrame({ x: 0, y: 0, width: 1024, height: 700 });
    expect(small.width).toBe(1024);
    expect(small.height).toBe(700);
    const saved = { x: 1700, y: 0, width: 1240, height: 820, maximized: true };
    expect(restoreFrame(saved, [laptop], laptop)).toEqual({ ...fallbackFrame(laptop), maximized: true });
    expect(restoreFrame({ ...saved, x: 100, y: 100 }, [laptop], laptop)).toEqual({ ...saved, x: 100, y: 100 });
  });

  it('writes through a temporary file and reads back', () => {
    const disk = new Map<string, string>();
    const io = {
      writeFileSync: ((p: string, d: string) => void disk.set(p, d)) as never,
      renameSync: ((a: string, b: string) => {
        disk.set(b, disk.get(a)!);
        disk.delete(a);
      }) as never,
    };
    saveWindowState('/s.json', { x: 1, y: 2, width: 800, height: 600, maximized: false }, io);
    expect([...disk.keys()]).toEqual(['/s.json']);
    const read = ((p: string) => {
      const v = disk.get(p);
      if (v === undefined) throw new Error('ENOENT');
      return v;
    }) as never;
    expect(loadWindowState('/s.json', read)).toEqual({ x: 1, y: 2, width: 800, height: 600, maximized: false });
    expect(loadWindowState('/missing.json', read)).toBeNull();
  });
});
