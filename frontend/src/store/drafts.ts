// Page drafts that survive leaving the page (switching step or mode): pasted
// lyrics, a parse preview waiting to be applied, a pairing in progress …
// Kept in memory per project (a reload starts fresh).

import { useCallback, useState } from 'react';
import { useApp } from './app';

const drafts = new Map<string, unknown>();

/** useState whose value is remembered per open project under `key`. */
export function useDraft<T>(key: string, initial: T | (() => T)): [T, (v: T | ((prev: T) => T)) => void] {
  const pid = useApp((s) => s.pid);
  const k = `${pid ?? ''}:${key}`;
  const [value, setValue] = useState<T>(() => (drafts.has(k) ? (drafts.get(k) as T)
    : typeof initial === 'function' ? (initial as () => T)() : initial));
  const set = useCallback((v: T | ((prev: T) => T)) => {
    setValue((prev) => {
      const next = typeof v === 'function' ? (v as (p: T) => T)(prev) : v;
      drafts.set(k, next);
      return next;
    });
  }, [k]);
  return [value, set];
}

/** Forget a project's drafts (e.g. it was deleted). */
export function clearDrafts(pid: string) {
  for (const k of [...drafts.keys()]) if (k.startsWith(`${pid}:`)) drafts.delete(k);
}
