// Subtitle styles as the server sends them (generated from kara_align.karaoke.styles / models).
import type { KaraokeStyle, SavedStyle } from '@/lib/types';
import styles from './fixtures/styles.json';

/** The built-in 默认 style (the simple-mode default). */
export const defaultStyle = () => structuredClone(styles.default) as unknown as KaraokeStyle;
/** Plain model defaults (a new project's style). */
export const plainStyle = () => structuredClone(styles.classic) as unknown as KaraokeStyle;
export const builtinSaved = (): SavedStyle => ({ id: 'default', name: '默认', builtin: true, updated: null, style: defaultStyle() });
