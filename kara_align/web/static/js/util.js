// Small DOM / formatting helpers shared by all panels.

const DEFERRED_PROPS = new Set(['value', 'checked', 'selected', 'disabled', 'indeterminate']);

/** Create an element: h('div', {class: 'x', onclick: fn}, child, 'text', [more]) */
// Native append() renders null/false as text; UI code passes optional
// children as `cond ? node : null`, so skip empty children everywhere.
for (const proto of [Element.prototype, DocumentFragment.prototype]) {
  const nativeAppend = proto.append;
  proto.append = function (...nodes) {
    return nativeAppend.apply(this, nodes.flat(Infinity).filter((n) => n !== null && n !== undefined && n !== false));
  };
}

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  const deferred = [];
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (DEFERRED_PROPS.has(k)) deferred.push([k, v]);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
  append(el, children);
  for (const [k, v] of deferred) el[k] = v;
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === undefined || c === null || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

/** ms -> "m:ss.mmm" (original audio timeline) */
export function fmtMs(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '—';
  const neg = ms < 0;
  const v = Math.abs(Math.round(ms));
  const m = Math.floor(v / 60000);
  const s = Math.floor((v % 60000) / 1000);
  const r = v % 1000;
  return `${neg ? '-' : ''}${m}:${String(s).padStart(2, '0')}.${String(r).padStart(3, '0')}`;
}

export function fmtShift(ms) {
  if (ms === null || ms === undefined) return '—';
  return `${ms > 0 ? '+' : ''}${ms} ms`;
}

/** Accept "1:23.456", "83.456s" or plain integer ms. Returns integer ms or null. */
export function parseTime(text) {
  const t = String(text).trim();
  if (!t) return null;
  let m = t.match(/^(-?)(\d+):(\d{1,2})(?:\.(\d{1,3}))?$/);
  if (m) {
    const frac = m[4] ? Number(m[4].padEnd(3, '0')) : 0;
    const v = Number(m[2]) * 60000 + Number(m[3]) * 1000 + frac;
    return m[1] ? -v : v;
  }
  m = t.match(/^(-?\d+(?:\.\d+)?)s$/);
  if (m) return Math.round(Number(m[1]) * 1000);
  if (/^-?\d+$/.test(t)) return Number(t);
  return null;
}

export function toast(message, kind = 'error', timeout = 6000) {
  const box = document.getElementById('toasts');
  const el = h('div', { class: `toast ${kind}` }, h('span', {}, message),
    h('button', { class: 'link', onclick: () => el.remove(), title: '关闭' }, '×'));
  box.appendChild(el);
  if (timeout) setTimeout(() => el.remove(), kind === 'error' ? timeout * 1.5 : timeout);
}

export function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

/** Copy text; returns true on success. Callers must show a manual fallback on false. */
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* fall through to execCommand */ }
  try {
    const ta = h('textarea', { style: { position: 'fixed', left: '-9999px' } });
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch (e) {
    return false;
  }
}

export function readFileText(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new Error(`读取文件失败：${file.name}`));
    r.readAsText(file, 'utf-8');
  });
}

/** A file <input> that calls onFile(file) */
export function fileButton(label, accept, onFile, { multiple = false } = {}) {
  const input = h('input', { type: 'file', accept, hidden: true, multiple });
  input.addEventListener('change', () => {
    const files = [...input.files];
    input.value = '';
    if (files.length) onFile(multiple ? files : files[0]);
  });
  const btn = h('button', { type: 'button', onclick: () => input.click() }, label);
  return h('span', { class: 'filebtn' }, btn, input);
}

export function isTyping(ev) {
  const t = ev.target;
  if (!t || !t.tagName) return false;
  const tag = t.tagName.toLowerCase();
  if (tag === 'textarea' || tag === 'select') return true;
  if (tag === 'input') {
    const type = (t.type || '').toLowerCase();
    return !['checkbox', 'radio', 'range', 'button', 'submit', 'file'].includes(type);
  }
  return t.isContentEditable;
}

export function numInput(value, onChange, attrs = {}) {
  const el = h('input', { type: 'number', value: value ?? '', ...attrs });
  el.addEventListener('change', () => {
    const v = el.value === '' ? null : Number(el.value);
    onChange(v, el);
  });
  return el;
}

/** Time input accepting m:ss.mmm or ms; shows ms; onChange(ms|null) */
export function timeInput(ms, onChange, attrs = {}) {
  const el = h('input', { type: 'text', class: 'time', value: ms ?? '', placeholder: 'ms 或 m:ss.mmm', ...attrs });
  el.title = ms === null || ms === undefined ? '' : fmtMs(ms);
  el.addEventListener('change', () => {
    const v = parseTime(el.value);
    if (el.value.trim() !== '' && v === null) {
      toast('时间格式无法识别，请输入毫秒整数或 m:ss.mmm');
      el.value = ms ?? '';
      return;
    }
    onChange(v, el);
  });
  return el;
}

export function badge(text, kind = '') {
  return h('span', { class: `badge ${kind}` }, text);
}

export function section(title, ...children) {
  return h('div', { class: 'card' }, title ? h('h3', {}, title) : null, ...children);
}

export function details(summary, open, ...children) {
  return h('details', { open }, h('summary', {}, summary), ...children);
}

export function pct(x) {
  return `${Math.round(x * 100)}%`;
}
