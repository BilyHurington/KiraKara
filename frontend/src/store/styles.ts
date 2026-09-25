// Saved subtitle styles (预设): an app-wide library on the server, shared by
// every project and the simple mode.

import { create } from 'zustand';
import { api } from '@/lib/api';
import type { KaraokeStyle, SavedStyle } from '@/lib/types';

interface LibState {
  saved: SavedStyle[] | null;
}

export const useLibrary = create<LibState>(() => ({ saved: null }));
const set = useLibrary.setState;
const get = useLibrary.getState;

export async function loadSavedStyles() {
  set({ saved: await api.get<SavedStyle[]>('/api/karaoke/styles') });
}

export async function saveStyle(name: string, style: KaraokeStyle, id?: string) {
  const s = await api.post<SavedStyle>('/api/karaoke/styles', { name, style, id });
  await loadSavedStyles();
  return s;
}

export async function deleteStyle(id: string) {
  await api.del(`/api/karaoke/styles/${id}`);
  set({ saved: (get().saved ?? []).filter((x) => x.id !== id) });
}

/** Two styles look the same (the preset name and the burn-in audio level don't count). */
export function sameLook(a: KaraokeStyle, b: KaraokeStyle) {
  const strip = (s: KaraokeStyle) => stable({ ...s, preset: '', output: undefined, version: 0 });
  return strip(a) === strip(b);
}

/** JSON with sorted keys, so key order never makes two equal styles differ. */
function stable(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stable).join(',')}]`;
  if (v && typeof v === 'object') {
    return `{${Object.keys(v).filter((k) => (v as Record<string, unknown>)[k] !== undefined).sort()
      .map((k) => `${JSON.stringify(k)}:${stable((v as Record<string, unknown>)[k])}`).join(',')}}`;
  }
  return JSON.stringify(v);
}
