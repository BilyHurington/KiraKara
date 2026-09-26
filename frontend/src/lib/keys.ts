// Keyboard helpers: which key presses global shortcuts may take, and IME-safe Enter.
//
// Space / Enter activate the focused button, switch, link …; arrow keys move
// inside radio groups, sliders, tabs and menus.  A global shortcut must never
// take those away, nor act inside an open dialog or while an input method is
// composing text.

type AnyKeyEvent = KeyboardEvent | { key: string; keyCode?: number; nativeEvent?: KeyboardEvent; isComposing?: boolean };

/** An input method (Japanese / Chinese IME) is composing: Enter / Escape belong to it. */
export function isComposing(e: AnyKeyEvent): boolean {
  const n = ('nativeEvent' in e && e.nativeEvent) ? e.nativeEvent : (e as KeyboardEvent);
  return !!(n?.isComposing || (e as KeyboardEvent).isComposing || n?.keyCode === 229 || (e as KeyboardEvent).keyCode === 229);
}

/** Enter that confirms (not the Enter that commits an IME composition). */
export function isEnter(e: AnyKeyEvent): boolean {
  return e.key === 'Enter' && !isComposing(e);
}

/** Escape that cancels (an IME uses Escape to drop its composition). */
export function isEscape(e: AnyKeyEvent): boolean {
  return e.key === 'Escape' && !isComposing(e);
}

const TYPING = 'input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="textbox"], [role="searchbox"], [role="combobox"], [role="spinbutton"]';
/** Space / Enter activate these. */
const ACTIVATABLE = `${TYPING}, button, a[href], summary, [role="button"], [role="link"], [role="switch"], [role="radio"], [role="checkbox"], `
  + '[role="tab"], [role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"], [role="option"], [role="slider"]';
/** Arrow keys move inside these. */
const ARROWS = `${TYPING}, [role="radio"], [role="radiogroup"], [role="slider"], [role="tab"], [role="tablist"], [role="menuitem"], `
  + '[role="menuitemcheckbox"], [role="menuitemradio"], [role="menu"], [role="option"], [role="listbox"], [role="separator"]';
/** Letters are typeahead in these. */
const LETTERS = `${TYPING}, [role="menu"], [role="menuitem"], [role="listbox"], [role="option"]`;

function closest(t: EventTarget | null, sel: string): boolean {
  return t instanceof Element && !!t.closest(sel);
}

export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.isContentEditable || closest(t, TYPING);
}

/** A modal dialog (or a popover / menu) is open: page shortcuts stay out of it. */
export function dialogOpen(t: EventTarget | null): boolean {
  if (closest(t, '[role="dialog"], [role="alertdialog"], [role="menu"]')) return true;
  return typeof document !== 'undefined'
    && !!document.querySelector('[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]');
}

/**
 * Should a page-wide shortcut leave this key press alone?  `key` decides what
 * the focused element itself would do with it (Space / Enter activate,
 * arrows navigate, letters are typeahead only in menus and lists).
 */
export function ignoreShortcut(e: KeyboardEvent, opts: { allowRepeat?: boolean } = {}): boolean {
  if (e.defaultPrevented) return true;
  if (!opts.allowRepeat && e.repeat) return true;
  if (isComposing(e)) return true;
  const t = e.target;
  if (dialogOpen(t)) return true;
  if (e.key === ' ' || e.code === 'Space' || e.key === 'Enter') return closest(t, ACTIVATABLE) || isTypingTarget(t);
  if (e.key.startsWith('Arrow') || e.key === 'Home' || e.key === 'End' || e.key === 'PageUp' || e.key === 'PageDown') {
    return closest(t, ARROWS) || isTypingTarget(t);
  }
  return closest(t, LETTERS) || isTypingTarget(t);
}

/** ⌘ on Apple platforms, Ctrl elsewhere (for shortcut labels). */
export const MOD_KEY: string = (() => {
  if (typeof navigator === 'undefined') return 'Ctrl';
  const p = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform || navigator.platform || navigator.userAgent;
  return /mac|iphone|ipad|ipod/i.test(p) ? '⌘' : 'Ctrl';
})();
export const SHIFT_KEY: string = MOD_KEY === '⌘' ? '⇧' : 'Shift';
