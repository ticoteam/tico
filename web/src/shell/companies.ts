import { useEffect, useState } from 'react';

/* The companies this person is in. Only the desktop app knows them: its bridge, `window.tico` (the part of
   desktop/src/bridge.d.ts the menu reads), lists them and switches the window to another one. In a browser there is
   no bridge, and a company is its own address: the menu shows this one only (platforms.md 4.10). */

export interface Company {
  id: string;
  name: string;
  /** The company the window shows. */
  active: boolean;
  /** The company of this page. */
  self: boolean;
  needs: number;
}
interface Bridge {
  companies: {
    list(): Promise<Company[]>;
    switchTo(id: string, url?: string): Promise<void>;
    add(server?: string): Promise<void>;
    onChange(cb: (all: Company[]) => void): () => void;
  };
}

/** The desktop bridge's companies, or null in a browser. */
export const companiesBridge = (): Bridge['companies'] | null =>
  (typeof window !== 'undefined' && (window as { tico?: Bridge }).tico?.companies) || null;

/** Every company, from the desktop; null in a browser (or until the list arrives). */
export function useCompanies(): Company[] | null {
  const [all, setAll] = useState<Company[] | null>(null);
  useEffect(() => {
    const b = companiesBridge();
    if (!b) return;
    let live = true;
    void b.list().then(
      (list) => live && setAll(list),
      () => undefined,
    );
    const off = b.onChange((list) => setAll(list));
    return () => {
      live = false;
      off();
    };
  }, []);
  return all;
}
