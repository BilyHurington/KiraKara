// AI reading round trip via any web chat: copy prompt → paste JSON result →
// validate → preview diff → apply selected lines. No LLM API, no keys.

import { POST, run } from '../api.js';
import { S, emit, lines, ppath, setPV } from '../state.js';
import { badge, copyText, details, fileButton, h, readFileText, toast } from '../util.js';

S.draft ||= { lyrics: '', link: '', track: '', ai: '' };

const STATUS = {
  ok: ['可应用', 'ok'],
  unchanged: ['无变化', ''],
  stale_text: ['原文已改变', 'warn'],
  unknown_line: ['未知行', 'err'],
  locked_skipped: ['人工锁定，跳过', 'info'],
  invalid: ['无效', 'err'],
};

export function renderAiBox() {
  const hasLines = lines().length > 0;
  const useSel = h('input', { type: 'checkbox', checked: !!S.aiOnlySelected, onchange: (e) => { S.aiOnlySelected = e.target.checked; } });
  const promptBox = S.aiPrompt ? promptView() : null;
  const resultTa = h('textarea', {
    rows: 6, value: S.draft.ai, placeholder: '把网页聊天返回的 JSON（可含说明文字/代码块）粘贴到这里',
    oninput: (e) => { S.draft.ai = e.target.value; },
  });
  return h('div', { class: 'ai-box' },
    h('p', { class: 'muted small' },
      '程序会填好当前歌词、行 ID、已有读音与返回格式。请自行粘贴到任意网页聊天，把得到的 JSON 贴回。',
      'AI 结果只作为注音补丁：不改时间、不改原文、不覆盖人工锁定的读音。'),
    h('div', { class: 'row' },
      h('button', { class: 'primary', disabled: !hasLines, onclick: (e) => copyPrompt(e.target) }, '复制 AI 提示词'),
      h('label', { class: 'small' }, useSel, '仅针对“歌词行”表中勾选的行')),
    promptBox,
    h('h4', {}, '粘贴 AI 结果'),
    resultTa,
    h('div', { class: 'row' },
      h('button', { disabled: !hasLines, onclick: (e) => validate(S.draft.ai, e.target) }, '校验并预览'),
      fileButton('上传 JSON…', '.json,.txt,application/json,text/plain', async (file) => {
        S.draft.ai = await readFileText(file);
        await validate(S.draft.ai);
      })),
    S.aiReport ? reportView() : null);
}

async function copyPrompt(btn) {
  const body = {};
  if (S.aiOnlySelected) {
    const ids = lines().filter((l) => S.selLines?.has(l.id)).map((l) => l.id);
    if (!ids.length) { toast('没有勾选任何行'); return; }
    body.line_ids = ids;
  }
  const r = await run(() => POST(ppath('/ai/prompt'), body), { busy: btn });
  if (!r) return;
  const ok = await copyText(r.prompt);
  S.aiPrompt = { ...r, copyFailed: !ok };
  if (ok) toast('提示词已复制到剪贴板，请粘贴到网页聊天', 'ok', 3000);
  else toast('自动复制失败，请在下方文本框中全选手动复制', 'info');
  emit('rerender');
}

function promptView() {
  const p = S.aiPrompt;
  const ta = h('textarea', { rows: 8, readonly: true, class: 'mono small', value: p.prompt, onfocus: (e) => e.target.select() });
  return details(p.copyFailed ? '提示词（自动复制失败，请手动全选复制）' : '查看提示词', !!p.copyFailed,
    h('p', { class: 'muted small' }, `歌词快照：${p.snapshot_id}（回传时用于校验原文是否已改变；仅修改 LRC 偏移不会阻止应用）`),
    ta,
    h('button', { onclick: async () => { if (await copyText(p.prompt)) toast('已复制', 'ok', 2000); else { ta.focus(); ta.select(); } } }, '再次复制'));
}

async function validate(text, busy) {
  if (!text.trim()) { toast('请先粘贴 AI 结果'); return; }
  const r = await run(() => POST(ppath('/ai/validate'), { text }), { busy });
  if (!r) return;
  S.aiReport = r;
  S.aiChosen = new Set((r.report.lines || []).filter((l) => l.status === 'ok').map((l) => l.line_id));
  emit('rerender');
}

function reportView() {
  const { report_id: reportId, report } = S.aiReport;
  const textOf = new Map(lines().map((l) => [l.id, l.text]));
  const rows = (report.lines || []).map((l) => {
    const [label, kind] = STATUS[l.status] || [l.status, ''];
    const applicable = l.status === 'ok';
    return h('tr', {},
      h('td', {}, h('input', {
        type: 'checkbox', disabled: !applicable, checked: applicable && S.aiChosen.has(l.line_id),
        onchange: (e) => { if (e.target.checked) S.aiChosen.add(l.line_id); else S.aiChosen.delete(l.line_id); },
      })),
      h('td', {}, textOf.get(l.line_id) ?? h('span', { class: 'muted' }, l.line_id)),
      h('td', {}, badge(label, kind), (l.reasons || []).map((r) => h('div', { class: 'small muted' }, r))),
      h('td', {}, (l.diff || []).length ? h('table', { class: 'diff' }, (l.diff || []).map((d) => h('tr', {},
        h('td', {}, d.surface),
        h('td', { class: 'old' }, d.old_reading ?? '—', d.old_units?.length ? ` [${d.old_units.join('/')}]` : ''),
        h('td', {}, '→'),
        h('td', { class: 'new' }, d.new_reading ?? '—', d.new_units?.length ? ` [${d.new_units.join('/')}]` : '')))) : h('span', { class: 'muted small' }, '—')));
  });
  return h('div', { class: 'report' },
    h('div', { class: 'row' },
      report.ok ? badge('格式有效', 'ok') : badge('存在错误', 'err'),
      report.snapshot_match ? badge('快照一致', 'ok') : badge('快照不一致：生成提示词后歌词或读音已变，请逐行核对，必要时重新生成', 'warn')),
    (report.errors || []).map((e) => h('div', { class: 'error-box' }, e)),
    (report.warnings || []).map((w) => h('div', { class: 'notice' }, w)),
    h('div', { class: 'scroll-box tall' }, h('table', { class: 'grid' },
      h('thead', {}, h('tr', {}, ['应用', '行', '状态', '读音变化'].map((t) => h('th', {}, t)))),
      h('tbody', {}, rows))),
    h('div', { class: 'row' },
      h('button', {
        class: 'primary',
        onclick: (e) => run(async () => {
          const ids = [...S.aiChosen];
          if (!ids.length) { toast('没有可应用的行'); return; }
          const pv = await POST(ppath('/ai/apply'), { report_id: reportId, line_ids: ids });
          S.aiReport = null;
          setPV(pv);
          toast(`已应用 ${ids.length} 行读音补丁`, 'ok', 3000);
        }, { busy: e.target }),
      }, '应用所选行'),
      h('button', { onclick: () => { S.aiReport = null; emit('rerender'); } }, '放弃')));
}
