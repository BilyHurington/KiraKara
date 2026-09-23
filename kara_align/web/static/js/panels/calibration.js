// Step 4 (LRC mode): first-onset calibration of the global shift.
//
//   base_i      = imported_start_i + embedded_shift
//   effective_i = base_i + user_shift
//   mark line k at marked_ms → user_shift = marked_ms - base_k (recomputed, never accumulated)

import { POST, run } from '../api.js';
import { play, positionMs, seek } from '../player.js';
import { S, emit, lineById, lines, ppath, project, setPV, view } from '../state.js';
import { badge, fmtMs, fmtShift, h, numInput, section, toast } from '../util.js';

export function timedLines() {
  return lines().filter((l) => l.kind === 'lyric' && l.sing && l.imported_start_ms != null);
}

export function ensureCalibLine() {
  const tl = timedLines();
  if (!S.calibLineId || !tl.some((l) => l.id === S.calibLineId)) S.calibLineId = tl[0]?.id || null;
  return S.calibLineId;
}

/** Keyboard "M": mark the selected line's first onset at the playhead. */
export async function markAtPlayhead() {
  const lid = ensureCalibLine();
  if (!lid) { toast('没有带时间的歌词行'); return; }
  const marked = Math.round(positionMs());
  const pv = await run(() => POST(ppath('/calibration/mark'), { line_id: lid, marked_ms: marked }));
  if (pv) {
    setPV(pv);
    toast(`已标记：${fmtMs(marked)}，全局平移 ${fmtShift(pv.project.calibration.user_shift_ms)}`, 'ok', 3000);
  }
}

export function renderCalibrationPanel(panel) {
  const p = project();
  if (p.mode !== 'lrc') {
    panel.append(section('LRC 首音校准',
      h('div', { class: 'notice' }, '当前为普通模式：不使用任何外部时间锚点，也不需要校准。如需使用 LRC 行时间，请在第 1 步切换到 LRC 增强模式。')));
    return;
  }
  const tl = timedLines();
  if (!tl.length) {
    panel.append(section('LRC 首音校准', h('div', { class: 'notice' }, '当前歌词没有有效的行时间，请在第 2 步导入带时间的 LRC，或切换到普通模式。')));
    return;
  }
  const lid = ensureCalibLine();
  const ln = lineById(lid);
  const cal = p.calibration;
  const doc = p.lyrics;
  const eff = view()?.effective_starts?.[lid];
  const base = ln.imported_start_ms + (doc.embedded_shift_ms || 0);
  const idx = tl.findIndex((l) => l.id === lid);

  const lineSel = h('select', { class: 'wide', value: lid, onchange: (e) => { S.calibLineId = e.target.value; emit('rerender'); } },
    tl.map((l, i) => {
      const e = view()?.effective_starts?.[l.id];
      return h('option', { value: l.id }, `#${i + 1}  ${fmtMs(e?.ms ?? l.imported_start_ms)}  ${l.text}`);
    }));

  const shiftTo = (v) => run(async () => setPV(await POST(ppath('/calibration/shift'), { user_shift_ms: Math.round(v) })));
  const nudge = (d) => shiftTo((cal.user_shift_ms || 0) + d);

  const quick = (label, i) => h('button', { class: 'small', onclick: () => { S.calibLineId = tl[i].id; emit('rerender'); } }, label);

  panel.append(
    section('LRC 首音校准',
      h('p', {}, '请标记', h('strong', {}, '所选歌词的第一处实际发音'), '——不是歌曲第一声，也不是字幕提前出现的时刻。音频不会被移动或剪掉前奏，只计算歌词锚点。'),
      h('div', { class: 'row' },
        h('button', { class: 'small', disabled: idx <= 0, onclick: () => { S.calibLineId = tl[idx - 1].id; emit('rerender'); } }, '◀ 上一行'),
        lineSel,
        h('button', { class: 'small', disabled: idx >= tl.length - 1, onclick: () => { S.calibLineId = tl[idx + 1].id; emit('rerender'); } }, '下一行 ▶')),
      h('div', { class: 'row small' }, '快速选择：', quick('首句', 0), quick('中段', Math.floor(tl.length / 2)), quick('末段', tl.length - 1),
        h('span', { class: 'muted' }, '默认使用首条演唱歌词；也可以选择任意更清楚的句子。')),
      h('table', { class: 'kv' }, h('tbody', {},
        kv('原始句首（LRC 中写的时间）', fmtMs(ln.imported_start_ms)),
        kv('内嵌 [offset]', doc.embedded_offset_raw != null
          ? `${doc.embedded_offset_raw} → 规范化平移 ${fmtShift(doc.embedded_shift_ms)}（只应用一次）`
          : '无', doc.embedded_offset_note),
        kv('base = 原始句首 + 内嵌平移', fmtMs(base)),
        kv('人工全局平移 user_shift', fmtShift(cal.user_shift_ms), '正值表示歌词后移'),
        kv('有效句首 effective', eff ? `${fmtMs(eff.ms)}${eff.kind === 'hard' ? '（人工锁定锚点）' : ''}` : '—'),
        ln.anchor ? kv('单行绝对锚点', `${fmtMs(ln.anchor.abs_ms)}（${ln.anchor.hard ? '硬' : '软'}，不随全局平移）`) : null,
        kv('校准状态', cal.confirmed ? badge('已确认', 'ok') : badge('未确认', 'warn'),
          cal.reference_line_id ? `参考行：${lineById(cal.reference_line_id)?.text || cal.reference_line_id}，标记于 ${fmtMs(cal.marked_ms)}` : ''))),
      h('div', { class: 'row' },
        h('button', { onclick: () => { seek(Math.max(0, (eff?.ms ?? base) - 2000)); play(); } }, '▶ 从有效句首前 2 秒播放'),
        h('button', { class: 'primary', onclick: () => markAtPlayhead(), title: '快捷键 M' }, '在播放头标记首个发音 (M)')),
      h('div', { class: 'row' },
        '微调全局平移：',
        [-50, -10, 10, 50].map((d) => h('button', { class: 'small', onclick: () => nudge(d) }, `${d > 0 ? '+' : ''}${d} ms`)),
        h('label', {}, '数值 ', numInput(cal.user_shift_ms, (v) => v !== null && shiftTo(v), { step: 1, style: { width: '7em' } }), ' ms'),
        h('button', { onclick: () => run(async () => setPV(await POST(ppath('/calibration/confirm-zero')))) }, '确认零偏移'),
        h('button', { onclick: () => run(async () => setPV(await POST(ppath('/calibration/undo')))), disabled: !(cal.history || []).length }, '撤销')),
      h('p', { class: 'muted small' }, '每次标记都会重新计算 user_shift，不会叠加旧值。例：原句首 12,300 ms 标在 12,950 得 +650；改标 13,000 得 +700。')),
    checksCard(tl),
  );
}

function kv(k, v, note) {
  return h('tr', {}, h('th', {}, k), h('td', {}, v, note ? h('div', { class: 'muted small' }, note) : null));
}

function checksCard(tl) {
  const cal = project().calibration;
  const issues = view()?.calibration_issues || [];
  return section('中段 / 末段检查',
    h('p', { class: 'muted small' }, '单点只能确定整体平移。选择中段、末段的句子，在其首个发音处添加检查点；若残差仍然较大，可能是版本或速度不一致，可添加单行锚点（第 2 步歌词行表），程序不会自动拉伸整曲时间。'),
    h('div', { class: 'row' },
      h('button', {
        onclick: () => run(async () => {
          const pv = await POST(ppath('/calibration/check'), { line_id: S.calibLineId, marked_ms: Math.round(positionMs()) });
          setPV(pv);
        }),
      }, '以播放头为当前所选行添加检查点')),
    cal.checks?.length ? h('table', { class: 'grid' },
      h('thead', {}, h('tr', {}, ['行', '标记', '残差（标记 − 有效）', ''].map((t) => h('th', {}, t)))),
      h('tbody', {}, cal.checks.map((c) => h('tr', {},
        h('td', {}, lineById(c.line_id)?.text || c.line_id),
        h('td', { class: 'mono' }, fmtMs(c.marked_ms)),
        h('td', { class: Math.abs(c.residual_ms) > 150 ? 'warn-text mono' : 'mono' }, fmtShift(c.residual_ms)),
        h('td', {}, h('button', { class: 'small', onclick: () => { seek(Math.max(0, c.marked_ms - 1500)); play(); } }, '▶')))))) : h('p', { class: 'muted small' }, '暂无检查点'),
    issues.map((i) => h('div', { class: i.severity === 'error' ? 'error-box' : 'notice' }, i.message)));
}

export function calibOverlayLines() {
  const p = project();
  if (!p || p.mode !== 'lrc') return [];
  const eff = view()?.effective_starts || {};
  const ls = lines();
  const out = [];
  ls.forEach((l, i) => {
    const e = eff[l.id];
    if (!e || l.kind !== 'lyric') return;
    out.push({ ms: e.ms, kind: e.kind, label: `${i + 1} ${l.text.slice(0, 12)}`, selected: l.id === S.calibLineId || l.id === S.selLineId, lineId: l.id });
  });
  return out;
}
