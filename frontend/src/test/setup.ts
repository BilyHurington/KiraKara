import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// jsdom lacks these browser APIs used by Radix / the waveform
class RO { observe() {} unobserve() {} disconnect() {} }
(globalThis as any).ResizeObserver ??= RO;
(globalThis as any).matchMedia ??= () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
Element.prototype.scrollIntoView ??= function () {};
(Element.prototype as any).hasPointerCapture ??= () => false;
(Element.prototype as any).releasePointerCapture ??= () => {};

// canvas: jsdom has no 2D context (the waveform renderer tolerates null)
HTMLCanvasElement.prototype.getContext = (() => null) as any;

// minimal Web Audio stub so the player can be constructed in tests
class FakeParam { value = 1; setTargetAtTime() {} }
class FakeNode { gain = new FakeParam(); playbackRate = new FakeParam(); connect(n: any) { return n; } disconnect() {} start() {} stop() {} }
class FakeAudioContext {
  currentTime = 0;
  destination = new FakeNode();
  createGain() { return new FakeNode(); }
  createBufferSource() { return new FakeNode(); }
  resume() { return Promise.resolve(); }
  decodeAudioData() { return Promise.resolve({ duration: 16, numberOfChannels: 1 }); }
}
(globalThis as any).AudioContext ??= FakeAudioContext;
