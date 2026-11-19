/* The theme, a device pref (`theme.v1`): light, dark, or "system" for the device's own. public/boot.js sets
   `<html data-theme>` before the first paint; this keeps it right afterwards (Settings, other tabs, the system). */
import { useEffect } from 'react';
import { usePref, writePref } from './prefs';

export type Theme = 'system' | 'light' | 'dark';
export const THEME_KEY = 'theme.v1';

const LIGHT = '(prefers-color-scheme: light)';
const systemLight = () => typeof window.matchMedia === 'function' && window.matchMedia(LIGHT).matches;

/** Dark unless chosen light, or "system" on a light device: what boot.js does. */
export function applyTheme(theme: unknown): void {
  const light = theme === 'light' || (theme === 'system' && systemLight());
  document.documentElement.setAttribute('data-theme', light ? 'light' : 'dark');
}

/** Saves the choice on this device and applies it at once. */
export function setThemeChoice(theme: Theme): void {
  writePref(THEME_KEY, theme);
  applyTheme(theme);
}

/** Mounted once at the app's root. Follows the pref, and the device while the pref is "system". */
export function useTheme(): void {
  const [theme] = usePref<Theme>(THEME_KEY, 'dark');
  useEffect(() => {
    applyTheme(theme);
    if (theme !== 'system' || typeof window.matchMedia !== 'function') return;
    const q = window.matchMedia(LIGHT);
    const follow = () => applyTheme('system');
    q.addEventListener('change', follow);
    return () => q.removeEventListener('change', follow);
  }, [theme]);
}
