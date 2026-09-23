// Handle to the dock's waveform so pages can reveal a region or read the view.

import type { Waveform } from './waveform';

export const waveformRef: { current: Waveform | null } = { current: null };

/** Scroll/zoom the dock waveform so [a, b] is visible. */
export function revealOnWaveform(a: number, b = a) {
  waveformRef.current?.reveal(a, b);
}
