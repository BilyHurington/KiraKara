// Step 6: manual review — unit times, issues, undo/redo, lock, local rerun, candidates.
//
// The raw model prediction is never modified; edits are manual overrides.
// Undo/redo uses the /restore endpoint with the recorded before/after ManualEdit.

import { DEL, POST, PUT, run } from '../api.js';
import { playRange, positionMs, seek } from '../player.js';
import { S, emit, lineById, lineIndex, loadResult, ppath, project, resultById, selectResult, setPV, view } from '../state.js';
import { badge, details, fmtMs, fmtShift, h, section, timeInput, toast } from '../util.js';
import { startAlign } from './align.js';

S.rerunLines ||= new Set();

const STATUS_LABEL = { ok: ['正常', 'ok'], failed: ['失败', 'err'], unaligned: ['未对齐', 'warn'], skipped: ['跳过', ''] };

// ------------------------------------------------------------------ edit operations

function unitOf(uid, result = S.result) {
  return result?.units.find((u) => u.unit_id === uid) || null;
}

const clone = (x) => (x ? JSON.parse(JSON.stringify(x)) : null);

async function recordOp(uid, action) {
  const rid = S.resultId;
  const before = clone(unitOf(uid)?.manual);
  const ut = await run(action);
  if (!ut) return null;
  S.undo.push({ rid, uid, before, after: clone(ut.manual) });
  if (S.undo.length > 500) S.undo.shift();
  S.redo = [];
  await loadResult(rid);
  emit('rerender');
  return ut;
}

export function editUnitTimes(uid, start, end) {
  if (!S.result) return null;
  if (start !== null && end !== null && end <= start) {
    toast('结束时间必须大于开始时间（区间为 [start, end)）');
    return null;
  }
  return recordOp(uid, () => PUT(ppath(`/results/${S.resultId}/units/${uid}`), { start_ms: start, end_ms: end, locked: true }));
}

function setLock(uid, locked) {
  return recordOp(uid, () => POST(ppath(`/results/${S.resultId}/units/${uid}/lock`), { locked }));
}

function clearManual(uid) {
  return recordOp(uid, () => DEL(ppath(`/results/${S.resultId}/units/${uid}/manual`)));
}

async function restore(op, manual) {
  if (op.rid !== S.resultId) selectResult(op.rid);
  const ut = await run(() => POST(ppath(`/results/${op.rid}/units/${op.uid}/restore`), { manual }));
  if (!ut) return false;
  await loadResult(op.rid);
  S.selUnitId = op.uid;
  emit('rerender');
  return true;
}

export async function undo() {
  const op = S.undo.pop();
  if (!op) { toast('没有可撤销的操作', 'info', 1500); return; }
  if (await restore(op, op.before)) S.redo.push(op); else S.undo.push(op);
  emit('rerender');
}

export async function redo() {
  const op = S.redo.pop();
  if (!op) { toast('没有可重做的操作', 'info', 1500); return; }
  if (await restore(op, op.after)) S.undo.push(op); else S.redo.push(op);
  emit('rerender');
}

/** Keyboard M in review: set the selected unit's start at the playhead. */
export function markUnitStart() {
  const u = unitOf(S.selUnitId);
  if (!u) { toast('请先选择一个单元'); return; }
  const t = Math.round(positionMs());
  const end = u.end_ms !== null && u.end_ms > t ? u.end_ms : t + 100;
  editUnitTimes(u.unit_id, t, end);
}

// ------------------------------------------------------------------ helpers

function unitsByLine(result) {
  const m = new Map();
  for (const u of result?.units || []) {
    if (!m.has(u.line_id)) m.set(u.line_id, []);
    m.get(u.line_id).push(u);
  }
  return m;
}

function issuesFor(result, lineId, unitId) {
  return (result?.issues || []).filter((i) => (unitId ? i.unit_id === unitId : i.line_id === lineId));
}

function lineRange(units) {
  const s = units.map((u) => u.start_ms).filter((x) => x !== null);
  const e = units.map((u) => u.end_ms).filter((x) => x !== null);
  return s.length && e.length ? [Math.min(...s), Math.max(...e)] : null;
}

export function selectUnit(uid) {
  const u = unitOf(uid);
  if (!u) return;
  S.selUnitId = uid;
  S.selLineId = u.line_id;
  emit('rerender');
}

function playUnit(u) {
  if (u.start_ms === null || u.end_ms === null) { toast('该单元没有时间'); return; }
  S.selUnitId = u.unit_id;
  S.selLineId = u.line_id;
  playRange(u.start_ms, u.end_ms, { padMs: 150 });
  emit('rerender');
}

function playLine(lineId, result = S.result) {
  const r = lineRange(unitsByLine(result).get(lineId) || []);
  if (!r) { toast('该行没有可用时间'); return; }
  S.selLineId = lineId;
  playRange(r[0], r[1], { padMs: 300 });
  emit('rerender');
}

// ------------------------------------------------------------------ render

export function renderReviewPanel(panel) {
  const results = view()?.results || [];
  if (!results.length || !S.result) {
    panel.append(section('人工检查', h('p', { class: 'muted' }, '还没有对齐结果，请先在第 5 步运行对齐。')));
    return;
  }
  const r = S.result;
  const byLine = unitsByLine(r);
  const lineIds = [...byLine.keys()];
  if (!S.selLineId || !byLine.has(S.selLineId)) S.selLineId = lineIds[0] || null;

  panel.append(
    headerCard(r, results),
    h('div', { class: 'review-cols' },
      h('div', { class: 'review-lines' }, lineList(r, byLine)),
      h('div', { class: 'review-detail' },
        S.selLineId ? lineDetail(r, S.selLineId, byLine.get(S.selLineId) || []) : null,
        compareCard(r))),
    issuesCard(r),
  );
}

function headerCard(r, results) {
  const p = project();
  return section('人工检查',
    h('div', { class: 'row' },
      h('label', {}, '结果 ', h('select', { value: r.id, onchange: (e) => { selectResult(e.target.value); emit('rerender'); } },
        results.map((x) => h('option', { value: x.id },
          `${x.created}${x.id === p.active_result_id ? ' [当前]' : ''}${x.coverage?.full ? '' : ' [局部]'}${x.stale ? ' [过期]' : ''}`)))),
      r.id !== p.active_result_id ? h('button', { onclick: () => run(async () => setPV(await POST(ppath(`/results/${r.id}/activate`)))) }, '设为当前结果') : badge('当前结果', 'ok'),
      h('button', { onclick: undo, disabled: !S.undo.length, title: 'Ctrl+Z' }, `撤销 (${S.undo.length})`),
      h('button', { onclick: redo, disabled: !S.redo.length, title: 'Ctrl+Shift+Z' }, `重做 (${S.redo.length})`)),
    r.stale ? h('div', { class: 'notice' }, `该结果已过期：${r.stale_reason || '输入已修改'}。仍可查看与修改，但建议重新对齐。`) : null,
    !r.coverage?.full ? h('div', { class: 'notice' }, `局部结果：仅覆盖 ${(r.coverage?.line_ids || []).length} 行${r.parent_result_id ? '（局部重跑，只作为候选，需在父结果中逐行“采用”）' : ''}`) : null,
    h('div', { class: 'muted small' },
      `模式 ${r.mode === 'lrc' ? 'LRC 增强' : '普通'} · 后端 ${r.backend?.name} · 模型 ${r.backend?.model_id}@${(r.backend?.model_revision || '').slice(0, 10)} · 转写 ${r.backend?.profile} · 音频 ${r.snapshot?.audio_role}`),
    h('div', { class: 'muted small' }, '单元颜色：', badge('正常', 'ok'), badge('人工', 'src-manual'), badge('失败', 'err'), badge('未对齐', 'warn'),
      ' · 点击单元定位并循环试听；在波形上拖动所选单元两端可修改起止；分数仅为诊断用，不代表真实正确概率。'));
}

function lineList(r, byLine) {
  const lt = new Map((r.lines || []).map((l) => [l.line_id, l]));
  const rows = [...byLine.entries()].map(([lid, units]) => {
    const ln = lineById(lid);
    const t = lt.get(lid);
    const bad = units.filter((u) => u.status !== 'ok').length;
    const manual = units.filter((u) => u.manual).length;
    const nIss = issuesFor(r, lid).length;
    const cands = (r.candidates || []).filter((c) => c.line_id === lid).length;
    return h('div', {
      class: `line-item ${lid === S.selLineId ? 'selected' : ''} ${bad ? 'has-bad' : ''}`,
      onclick: (e) => { if (e.target.tagName === 'INPUT') return; S.selLineId = lid; S.selUnitId = null; emit('rerender'); },
      ondblclick: () => playLine(lid),
    },
    h('input', {
      type: 'checkbox', title: '选中用于局部重跑', checked: S.rerunLines.has(lid),
      onchange: (e) => { if (e.target.checked) S.rerunLines.add(lid); else S.rerunLines.delete(lid); emit('rerender'); },
    }),
    h('span', { class: 'idx' }, lineIndex(lid) + 1),
    h('span', { class: 'mono small' }, fmtMs(t?.start_ms)),
    h('span', { class: 'text' }, ln?.text || lid),
    bad ? badge(`${bad} 异常`, 'err') : null,
    nIss ? badge(`${nIss} 提示`, 'warn') : null,
    manual ? badge(`${manual} 人工`, 'src-manual') : null,
    cands ? badge(`${cands} 候选`, 'info') : null);
  });
  const sel = [...S.rerunLines].filter((id) => byLine.has(id));
  return h('div', {},
    h('div', { class: 'row' },
      h('button', {
        disabled: !sel.length,
        onclick: (e) => startAlign({
          lineIds: sel, busy: e.target,
          onResult: (rid) => { S.compareWith = rid; S.rerunLines.clear(); toast('局部重跑完成：请在右侧对比后逐行采用', 'ok'); emit('rerender'); },
        }),
      }, `局部重跑所选 ${sel.length} 行`),
      h('button', { class: 'small', onclick: () => { for (const [lid, us] of byLine) if (us.some((u) => u.status !== 'ok') || issuesFor(r, lid).length) S.rerunLines.add(lid); emit('rerender'); } }, '勾选异常行'),
      h('button', { class: 'small', onclick: () => { S.rerunLines.clear(); emit('rerender'); } }, '清除')),
    h('div', { class: 'line-list' }, rows));
}

function lineDetail(r, lid, units) {
  const ln = lineById(lid);
  const t = (r.lines || []).find((l) => l.line_id === lid);
  const segSurface = new Map();
  for (const seg of ln?.segments || []) for (const u of seg.units || []) segSurface.set(u.id, u.surface || seg.surface);
  const rows = units.map((u) => {
    const [label, kind] = STATUS_LABEL[u.status] || [u.status, ''];
    const iss = issuesFor(r, lid, u.unit_id);
    const changed = u.manual && (u.model_start_ms !== u.start_ms || u.model_end_ms !== u.end_ms);
    return h('tr', { class: `${u.unit_id === S.selUnitId ? 'selected' : ''} ${u.status !== 'ok' ? 'bad' : ''}`, onclick: (e) => {
      if (['INPUT', 'BUTTON'].includes(e.target.tagName)) return;
      S.selUnitId = u.unit_id;
      if (u.start_ms !== null) seek(u.start_ms);
      emit('rerender');
    } },
    h('td', {}, h('button', { class: 'small', onclick: () => playUnit(u), title: '循环试听该单元' }, '▶')),
    h('td', {}, h('span', { class: 'surface' }, segSurface.get(u.unit_id) || ''), ' ', h('span', { class: 'reading' }, u.reading)),
    h('td', {}, timeInput(u.start_ms, (v) => editUnitTimes(u.unit_id, v, u.end_ms)), h('div', { class: 'muted small mono' }, fmtMs(u.start_ms))),
    h('td', {}, timeInput(u.end_ms, (v) => editUnitTimes(u.unit_id, u.start_ms, v)), h('div', { class: 'muted small mono' }, fmtMs(u.end_ms))),
    h('td', {}, badge(label, kind), u.reason ? h('div', { class: 'small muted' }, u.reason) : null,
      changed ? h('div', { class: 'small muted' }, `模型：${fmtMs(u.model_start_ms)}–${fmtMs(u.model_end_ms)}`) : null,
      u.tail ? h('div', { class: 'small muted', title: u.tail.reason }, `尾音 ${u.tail.method}：${fmtMs(u.tail.original_end_ms)}→${fmtMs(u.tail.new_end_ms)}`) : null),
    h('td', { class: 'small' }, (u.flags || []).join(' '), iss.map((i) => h('div', { class: 'warn-text' }, i.message))),
    h('td', { class: 'nowrap' },
      h('label', { class: 'small', title: '锁定：新结果与重跑不会覆盖' },
        h('input', { type: 'checkbox', checked: !!u.manual?.locked, onchange: (e) => setLock(u.unit_id, e.target.checked) }), '锁定'),
      u.manual ? h('button', { class: 'small', onclick: () => clearManual(u.unit_id), title: '清除人工修改，回到模型时间（历史保留）' }, '清除人工') : null));
  });
  const cands = (r.candidates || []).filter((c) => c.line_id === lid);
  return section(`#${lineIndex(lid) + 1} ${ln?.text || lid}`,
    h('div', { class: 'row small' },
      h('button', { onclick: () => playLine(lid) }, '▶ 循环试听整行'),
      t ? h('span', { class: 'mono' }, `${fmtMs(t.start_ms)} – ${fmtMs(t.end_ms)}`) : null,
      t?.anchor_ms != null ? h('span', {}, `锚点 ${fmtMs(t.anchor_ms)}（${t.anchor_kind === 'hard' ? '硬' : '软'}）残差 ${fmtShift(t.anchor_residual_ms)}`) : null,
      t?.window_ms ? h('span', { class: 'muted' }, `窗口 ${fmtMs(t.window_ms[0])}–${fmtMs(t.window_ms[1])}`) : null,
      t?.context_line_ids?.length ? h('span', { class: 'muted' }, `联合对齐上下文 ${t.context_line_ids.length} 行`) : null,
      t?.audio_role ? h('span', { class: 'muted' }, `音频 ${t.audio_role}`) : null,
      t?.candidate ? h('span', { class: 'muted' }, `采用候选 ${t.candidate}`) : null,
      (t?.flags || []).map((f) => badge(f, 'warn'))),
    t?.reason ? h('div', { class: 'notice small' }, t.reason) : null,
    h('div', { class: 'scroll-box' }, h('table', { class: 'grid units' },
      h('thead', {}, h('tr', {}, ['', '单元', '开始 (ms)', '结束 (ms)', '状态', '标记/提示', '人工'].map((x) => h('th', {}, x)))),
      h('tbody', {}, rows))),
    h('p', { class: 'muted small' }, '快捷键：M 把所选单元的开始设为播放头；Ctrl+Z 撤销；Ctrl+Shift+Z 重做。失败/未对齐单元不会被填充伪时间，可手动输入。'),
    cands.length ? candidatesView(r, lid, cands) : null);
}

function candidatesView(r, lid, cands) {
  return details(`候选结果（${cands.length}）：明显分歧时保留供试听，不取平均`, true,
    h('table', { class: 'grid' }, h('tbody', {}, cands.map((c) => {
      const rg = lineRange(c.units);
      const showing = S.candidateView === c.id;
      return h('tr', { class: showing ? 'selected' : '' },
        h('td', {}, c.label),
        h('td', { class: 'mono small' }, rg ? `${fmtMs(rg[0])}–${fmtMs(rg[1])}` : '无时间'),
        h('td', { class: 'small muted' }, Object.entries(c.summary || {}).map(([k, v]) => `${k}: ${typeof v === 'number' ? Math.round(v * 1000) / 1000 : JSON.stringify(v)}`).join(' · ')),
        h('td', { class: 'nowrap' },
          h('button', {
            class: 'small', disabled: !rg,
            onclick: () => { S.candidateView = showing ? null : c.id; if (!showing && rg) playRange(rg[0], rg[1], { padMs: 300 }); emit('rerender'); },
          }, showing ? '隐藏' : '试听/显示'),
          h('button', {
            class: 'small',
            onclick: (e) => run(async () => {
              await POST(ppath(`/results/${r.id}/adopt`), { candidate_id: c.id, line_ids: [lid] });
              S.candidateView = null;
              await loadResult(r.id);
              toast('已采用候选（人工锁定的单元未改变）', 'ok', 2500);
              emit('rerender');
            }, { busy: e.target }),
          }, '采用')));
    }))));
}

function compareCard(r) {
  const children = (project().results || []).filter((x) => x.parent_result_id === r.id);
  if (!children.length) return null;
  if (!S.compareWith || !children.some((c) => c.id === S.compareWith)) S.compareWith = children[children.length - 1].id;
  const child = resultById(S.compareWith);
  if (!child) return null;
  const parentBy = unitsByLine(r);
  const childBy = unitsByLine(child);
  const lineIds = child.coverage?.line_ids?.length ? child.coverage.line_ids : [...childBy.keys()];
  const rows = lineIds.map((lid) => {
    const pu = parentBy.get(lid) || [];
    const cu = childBy.get(lid) || [];
    const pr = lineRange(pu);
    const cr = lineRange(cu);
    let maxDiff = null;
    const pMap = new Map(pu.map((u) => [u.unit_id, u]));
    for (const u of cu) {
      const p = pMap.get(u.unit_id);
      if (!p) continue;
      for (const k of ['start_ms', 'end_ms']) {
        if (p[k] !== null && u[k] !== null) maxDiff = Math.max(maxDiff ?? 0, Math.abs(p[k] - u[k]));
      }
    }
    const locked = pu.filter((u) => u.manual?.locked).length;
    return h('tr', {},
      h('td', {}, `#${lineIndex(lid) + 1} ${lineById(lid)?.text || lid}`),
      h('td', { class: 'mono small' }, pr ? `${fmtMs(pr[0])}–${fmtMs(pr[1])}` : '—'),
      h('td', { class: 'mono small' }, cr ? `${fmtMs(cr[0])}–${fmtMs(cr[1])}` : '—'),
      h('td', { class: 'mono small' }, maxDiff === null ? '—' : `${maxDiff} ms`),
      h('td', { class: 'small' }, `${cu.filter((u) => u.status !== 'ok').length} 异常`, locked ? h('div', { class: 'muted' }, `${locked} 个锁定单元不会被改变`) : null),
      h('td', { class: 'nowrap' },
        h('button', { class: 'small', onclick: () => playLine(lid, r) }, '听原'),
        h('button', { class: 'small', onclick: () => playLine(lid, child) }, '听新'),
        h('button', { class: 'small primary', onclick: (e) => adopt(r, child, [lid], e.target) }, '采用')));
  });
  return section('局部重跑对比',
    h('div', { class: 'row' },
      h('select', { value: S.compareWith, onchange: (e) => { S.compareWith = e.target.value; emit('rerender'); } },
        children.map((c) => h('option', { value: c.id }, `${c.created}（${(c.coverage?.line_ids || []).length} 行）`))),
      h('button', { onclick: (e) => adopt(r, child, lineIds, e.target) }, '全部采用'),
      h('span', { class: 'muted small' }, '重跑只给出候选；采用时人工锁定的单元保持不变。')),
    h('div', { class: 'scroll-box' }, h('table', { class: 'grid' },
      h('thead', {}, h('tr', {}, ['行', '当前', '重跑', '最大差异', '', ''].map((x) => h('th', {}, x)))),
      h('tbody', {}, rows))));
}

function adopt(parent, child, lineIds, busy) {
  return run(async () => {
    await POST(ppath(`/results/${parent.id}/adopt`), { from_result_id: child.id, line_ids: lineIds });
    await loadResult(parent.id);
    toast(`已采用 ${lineIds.length} 行`, 'ok', 2500);
    emit('rerender');
  }, { busy });
}

function issuesCard(r) {
  const issues = r.issues || [];
  if (!issues.length) return section('问题列表', h('p', { class: 'muted' }, '没有检测到异常（阈值可配置，仅作诊断）'));
  const sev = { error: 'err', warning: 'warn', info: 'info' };
  return section(`问题列表（${issues.length}）`,
    h('div', { class: 'scroll-box' }, h('table', { class: 'grid' },
      h('tbody', {}, issues.map((i) => h('tr', {
        class: 'clickable',
        onclick: () => {
          if (i.unit_id) {
            S.selUnitId = i.unit_id;
            const u = unitOf(i.unit_id);
            if (u) { S.selLineId = u.line_id; if (u.start_ms !== null) seek(u.start_ms); }
          } else if (i.line_id) {
            S.selLineId = i.line_id;
            const rg = lineRange(unitsByLine(r).get(i.line_id) || []);
            if (rg) seek(rg[0]);
          }
          emit('rerender');
        },
      },
      h('td', {}, badge(i.severity, sev[i.severity] || '')),
      h('td', { class: 'mono small' }, i.code),
      h('td', {}, i.line_id ? `#${lineIndex(i.line_id) + 1} ` : '', i.message)))))));
}

/** Waveform overlay data for the current result (and a shown candidate). */
export function reviewOverlayUnits() {
  const r = S.result;
  if (!r) return { units: [], candUnits: [] };
  const mk = (u, color) => ({
    id: u.unit_id, start: u.start_ms, end: u.end_ms, label: u.reading, locked: !!u.manual?.locked,
    color: color || (u.manual ? 'manual' : u.status), editable: !color && u.start_ms !== null && u.end_ms !== null,
  });
  const units = r.units.map((u) => mk(u));
  let candUnits = [];
  if (S.candidateView) {
    const c = (r.candidates || []).find((x) => x.id === S.candidateView);
    if (c) candUnits = c.units.map((u) => mk(u, 'candidate'));
  }
  return { units, candUnits };
}

export { toast };
