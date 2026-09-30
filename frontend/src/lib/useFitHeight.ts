// A sticky column as tall as the visible part of the page it scrolls in (the page minus the top bar
// and the player below): set as --fit-h on the element, used with e.g. lg:max-h-[var(--fit-h)].

import { useCallback, useRef } from 'react';

function scrollParent(el: HTMLElement): HTMLElement | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const y = getComputedStyle(p).overflowY;
    if (y === 'auto' || y === 'scroll') return p;
  }
  return null;
}

/** A callback ref: the element gets --fit-h while it is mounted. */
export function useFitHeight(margin = 32) {
  const stop = useRef<(() => void) | null>(null);
  return useCallback((el: HTMLElement | null) => {
    stop.current?.();
    stop.current = null;
    const port = el && scrollParent(el);
    if (!el || !port || typeof ResizeObserver === 'undefined') return;
    const fit = () => el.style.setProperty('--fit-h', `${Math.max(320, port.clientHeight - margin)}px`);
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(port);
    stop.current = () => ro.disconnect();
  }, [margin]);
}
