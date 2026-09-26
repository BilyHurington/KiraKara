// Thin fetch wrapper: JSON in/out, server `detail` surfaced as a readable
// (Chinese) error message.  It also tracks whether the local server answers,
// for the app-wide connection banner.

import { create } from 'zustand';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// ------------------------------------------------------------------ connection

interface ConnState {
  /** the last request could not reach the server at all */
  down: boolean;
  /** when it went down (ms), for the banner */
  since: number | null;
}

export const useConnection = create<ConnState>(() => ({ down: false, since: null }));

export function markConnection(ok: boolean) {
  const s = useConnection.getState();
  if (ok && s.down) useConnection.setState({ down: false, since: null });
  else if (!ok && !s.down) useConnection.setState({ down: true, since: Date.now() });
}

export const OFFLINE_MESSAGE = '无法连接到本地服务，请确认 kara-align serve 正在运行';

// ------------------------------------------------------------------ readable errors

const PY_PREFIX = /^(?:[A-Za-z_][\w.]*\.)?(?:[A-Z]\w*(?:Error|Exception|Exit|Failure|Warning)|Cancelled|KeyError|StopIteration|AssertionError):\s*/;

/**
 * Error text meant for people: drops Python class prefixes ("ValueError: …"),
 * quotes of KeyError and a traceback tail, and maps bare HTTP / English texts.
 */
export function readableError(msg: unknown): string {
  let t = typeof msg === 'string' ? msg : msg instanceof Error ? msg.message : msg == null ? '' : String(msg);
  t = t.trim();
  const tb = t.indexOf('Traceback (most recent call last)');
  if (tb > 0) t = t.slice(0, tb).trim();
  for (let i = 0; i < 3 && PY_PREFIX.test(t); i++) t = t.replace(PY_PREFIX, '');
  if (/^'.*'$/.test(t)) t = `缺少 ${t}`;
  if (!t) return '未知错误';
  if (/^Internal Server Error$/i.test(t)) return '本地服务内部错误（HTTP 500），请查看运行 kara-align serve 的终端日志';
  if (/^Not Found$/i.test(t)) return '找不到请求的内容（HTTP 404）';
  if (/^Method Not Allowed$/i.test(t)) return '本地服务不支持这个操作（HTTP 405），请确认前端与服务版本一致';
  if (/^Request Entity Too Large$/i.test(t)) return '文件或文本过大';
  return t;
}

const PYDANTIC_TYPE: Record<string, string> = {
  missing: '缺少这一项',
  int_parsing: '应为整数', int_type: '应为整数', float_parsing: '应为数字', float_type: '应为数字',
  string_type: '应为文字', bool_parsing: '应为是 / 否', bool_type: '应为是 / 否',
  literal_error: '取值无效', enum: '取值无效', json_invalid: '不是有效的 JSON',
  greater_than: '数值太小', greater_than_equal: '数值太小', less_than: '数值太大', less_than_equal: '数值太大',
  string_too_short: '太短', string_too_long: '太长', too_short: '太少', too_long: '太多',
  finite_number: '必须是有限的数字', dict_type: '格式不正确', list_type: '格式不正确', model_type: '格式不正确',
};

/** FastAPI 422 detail list → one readable line. */
export function validationMessage(detail: { loc?: (string | number)[]; msg?: string; type?: string }[]): string {
  const parts = detail.slice(0, 3).map((d) => {
    const field = (d.loc ?? []).filter((x) => x !== 'body' && x !== 'query' && x !== 'path').join('.');
    const what = (d.type && PYDANTIC_TYPE[d.type]) ?? (d.msg ? readableError(d.msg) : '格式不正确');
    return field ? `${field}：${what}` : what;
  });
  return `请求内容不符合要求（${parts.join('；')}${detail.length > 3 ? ' …' : ''}）`;
}

// ------------------------------------------------------------------ requests

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    (init.headers as Record<string, string>)['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    markConnection(false);
    throw new ApiError(OFFLINE_MESSAGE, 0);
  }
  markConnection(true);
  const text = await res.text();
  let data: any = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) throw new ApiError(errorText(res.status, data), res.status);
  return data as T;
}

/** The error text of a failed response (JSON `detail`, a 422 list or plain text). */
export function errorText(status: number, data: any): string {
  const detail = data && typeof data === 'object' ? data.detail : data;
  if (Array.isArray(detail)) return validationMessage(detail);
  if (typeof detail === 'string' && detail.trim() && !/^\s*</.test(detail)) return readableError(detail);
  if (status === 413) return '文件或文本过大';
  if (status === 404) return '找不到请求的内容（HTTP 404）';
  if (status >= 500) return `本地服务内部错误（HTTP ${status}），请查看运行 kara-align serve 的终端日志`;
  return `请求失败（HTTP ${status}）`;
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body?: unknown) => request<T>('POST', url, body ?? {}),
  put: <T>(url: string, body?: unknown) => request<T>('PUT', url, body ?? {}),
  patch: <T>(url: string, body?: unknown) => request<T>('PATCH', url, body ?? {}),
  del: <T>(url: string) => request<T>('DELETE', url),
};

/**
 * Upload with progress (fetch cannot report upload progress).  `onProgress`
 * gets 0–1; errors are ApiErrors like `api.post`.
 */
export function uploadWithProgress<T>(url: string, form: FormData, onProgress: (f: number) => void): Promise<T> {
  if (typeof XMLHttpRequest === 'undefined') return api.post<T>(url, form);
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onerror = () => { markConnection(false); reject(new ApiError(OFFLINE_MESSAGE, 0)); };
    xhr.onload = () => {
      markConnection(true);
      let data: any = null;
      try { data = xhr.responseText ? JSON.parse(xhr.responseText) : null; } catch { data = xhr.responseText; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T);
      else reject(new ApiError(errorText(xhr.status, data), xhr.status));
    };
    xhr.send(form);
  });
}

/**
 * Download a server file without leaving the page: failures become a readable
 * error instead of a JSON page.  Text exports are fetched and saved from a
 * blob; big files (videos, WAV, packages) are checked first and then handed to
 * the browser's own download (so they are never held in memory).
 */
export async function downloadFile(url: string, opts: { big?: boolean; filename?: string; check?: boolean } = {}): Promise<void> {
  if (opts.check === false) {
    // generated on request (a project package): checking first would build it twice
    clickDownload(url, opts.filename ?? '');
    return;
  }
  let res: Response;
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  try {
    res = await fetch(url, { signal: ctrl?.signal });
  } catch {
    markConnection(false);
    throw new ApiError(OFFLINE_MESSAGE, 0);
  }
  markConnection(true);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    let data: any = text;
    try { data = JSON.parse(text); } catch { /* plain text */ }
    throw new ApiError(errorText(res.status, data), res.status);
  }
  const name = opts.filename ?? filenameFrom(res.headers.get('Content-Disposition')) ?? url.split('?')[0].split('/').pop() ?? 'download';
  if (opts.big) {
    ctrl?.abort();  // it exists: let the browser stream it to disk
    clickDownload(url, name);
  } else {
    const href = URL.createObjectURL(await res.blob());
    clickDownload(href, name);
    setTimeout(() => URL.revokeObjectURL(href), 60_000);
  }
}

function clickDownload(href: string, name: string) {
  const a = document.createElement('a');
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function filenameFrom(cd: string | null): string | null {
  if (!cd) return null;
  const star = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i.exec(cd);
  if (star) {
    try { return decodeURIComponent(star[1].trim().replace(/^"|"$/g, '')); } catch { /* fall through */ }
  }
  const m = /filename\s*=\s*"?([^";]+)"?/i.exec(cd);
  return m ? m[1] : null;
}
