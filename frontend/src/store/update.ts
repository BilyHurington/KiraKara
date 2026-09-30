// A newer MiliKara? Asked once when the app opens (the server only contacts GitHub when the settings
// allow it, at most every few hours), and again from the settings page.

import { create } from 'zustand';
import { api } from '@/lib/api';
import type { UpdateInfo } from '@/lib/types';

export const useUpdate = create<{ info: UpdateInfo | null }>(() => ({ info: null }));

export async function loadUpdate(refresh = false) {
  const info = await api.get<UpdateInfo>(`/api/update${refresh ? '?refresh=1' : ''}`);
  useUpdate.setState({ info });
  return info;
}
