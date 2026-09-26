// Which panel of the 注音与分离 page is shown (remembered per browser), and a
// way for other pages to open it on a given panel.

import { setStep } from '@/store/app';

export type EnhanceTab = 'readings' | 'ai' | 'separation';

export const ENHANCE_TAB_KEY = 'kara.enhanceTab';

export function loadEnhanceTab(): EnhanceTab {
  try {
    const v = localStorage.getItem(ENHANCE_TAB_KEY);
    if (v === 'readings' || v === 'ai' || v === 'separation') return v;
  } catch { /* ignore */ }
  return 'readings';
}

export function saveEnhanceTab(t: EnhanceTab) {
  try { localStorage.setItem(ENHANCE_TAB_KEY, t); } catch { /* ignore */ }
}

/** Go to 注音与分离 on a panel (e.g. “重新分离” from an outdated stem). */
export function openEnhance(tab: EnhanceTab) {
  saveEnhanceTab(tab);
  setStep('enhance');
}
