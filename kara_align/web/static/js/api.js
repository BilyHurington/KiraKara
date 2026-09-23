// fetch wrapper: JSON in/out, server `detail` surfaced as the error message.

import { toast } from './util.js';

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function detailText(detail) {
  if (detail === undefined || detail === null) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors
    return detail.map((d) => (d && d.msg ? `${(d.loc || []).join('.')}: ${d.msg}` : JSON.stringify(d))).join('；');
  }
  return JSON.stringify(detail);
}

export async function api(method, path, body) {
  const init = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new ApiError(`无法连接本地服务：${e.message}`, 0);
  }
  const ct = res.headers.get('content-type') || '';
  const data = ct.includes('application/json') ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    const d = data && typeof data === 'object' ? detailText(data.detail) : String(data || '');
    throw new ApiError(`${d || res.statusText}（HTTP ${res.status}）`, res.status);
  }
  return data;
}

export const GET = (p) => api('GET', p);
export const POST = (p, b = {}) => api('POST', p, b);
export const PUT = (p, b = {}) => api('PUT', p, b);
export const PATCH = (p, b = {}) => api('PATCH', p, b);
export const DEL = (p) => api('DELETE', p);

/** Run an async action; errors become toasts. Returns the result or undefined. */
export async function run(fn, { busy } = {}) {
  if (busy) busy.disabled = true;
  try {
    return await fn();
  } catch (e) {
    console.error(e);
    toast(e.message || String(e));
    return undefined;
  } finally {
    if (busy) busy.disabled = false;
  }
}
