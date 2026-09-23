// Step 5: run alignment (real job status, cancel, failure reason) and manage results.

import { PATCH, POST, run } from '../api.js';
import { S, audioAvailable, cancelJob, emit, lines, ppath, project, refreshProject, selectResult, setPV, trackJob, view } from '../state.js';
import { badge, fileButton, h, readFileText, section, toast } from '../util.js';
import { resultsCard, statusText } from './project.js';

/** Submit an alignment job; line_ids => local rerun (new partial result, never overwrites). */
export async function startAlign({ lineIds = null, busy = null, onResult = null } = {}) {
  const p = project();
  const cfg = p.config || {};
  const body = { audio_role: cfg.audio_role || 'original', config: cfg };
  if (lineIds) body.line_ids = lineIds;
  const job = await run(() => POST(ppath('/align'), body), { busy });
  if (!job) return;
  trackJob(job, {
    label: lineIds ? `局部重跑（${lineIds.length} 行）` : '对齐',
    onDone: async (j) => {
      await refreshProject();
      if (j.status === 'succeeded' && j.output?.result_id) onResult?.(j.output.result_id);
    },
  });
}

function saveConfig(patch) {
  const cfg = structuredClone(project().config || {});
  for (const [k, v] of Object.entries(patch)) {
    if (v && typeof v === 'object' && !Array.isArray(v)) cfg[k] = { ...(cfg[k] || {}), ...v };
    else cfg[k] = v;
  }
  return run(async () => setPV(await PATCH(ppath(), { config: cfg })));
}

function numField(label, value, onChange, attrs = {}) {
  const el = h('input', { type: 'number', value: value ?? '', style: { width: '6.5em' }, ...attrs });
  el.addEventListener('change', () => el.value !== '' && onChange(Number(el.value)));
  return h('label', { class: 'field' }, label, el);
}

export function renderAlignPanel(panel) {
  const p = project();
  const cfg = p.config || {};
  const dec = cfg.decode || {};
  const backends = S.info?.backends || [];
  const sung = lines().filter((l) => l.sing && l.kind === 'lyric');
  const hasUnits = sung.some((l) => (l.segments || []).some((s) => s.units?.length));
  const role = cfg.audio_role || 'original';
  const preflight = [];
  if (!sung.length) preflight.push(['err', '没有参与对齐的歌词行']);
  else if (!hasUnits) preflight.push(['warn', '歌词尚无发音单元：请先在第 3 步运行规则注音或应用 AI 注音']);
  if (!audioAvailable(role)) preflight.push(['err', `缺少${role === 'vocals' ? '人声' : '原曲'}音频`]);
  if (p.mode === 'lrc' && !p.calibration?.confirmed) preflight.push(['warn', 'LRC 增强模式：尚未确认首音校准（第 4 步），锚点可能整体偏移']);
  for (const w of view()?.capability_warnings || []) preflight.push(['warn', w]);
  const jobs = Object.values(S.jobs).filter((j) => j.kind === 'align' && j.project_id === S.pid);
  const bk = backends.find((b) => b.name === cfg.backend);

  panel.append(
    section('对齐设置',
      h('div', { class: 'row' },
        h('span', {}, '对齐输入音频：'),
        ['original', 'vocals'].map((r) => h('label', {},
          h('input', { type: 'radio', name: 'arole', value: r, checked: role === r, disabled: !audioAvailable(r), onchange: () => saveConfig({ audio_role: r }) }),
          r === 'original' ? '原曲' : '人声（未衰减）', !audioAvailable(r) ? h('span', { class: 'muted small' }, '（无）') : null))),
      h('div', { class: 'row' },
        h('label', {}, '声学后端 ', h('select', { value: cfg.backend, onchange: (e) => saveConfig({ backend: e.target.value }) },
          backends.map((b) => h('option', { value: b.name, disabled: !b.available }, `${b.name}${b.available ? '' : '（不可用）'} — ${b.description || ''}`)))),
        bk ? h('span', { class: 'muted small' }, `模型 ${bk.default_model || ''} · 语言 ${(bk.languages || []).join('/')} · 许可 ${bk.license || '未知'}`) : null),
      h('div', { class: 'row' },
        h('label', { class: 'field' }, '模型 ID（可选覆盖）', h('input', {
          type: 'text', value: cfg.model_id || '', placeholder: '默认权重',
          onchange: (e) => saveConfig({ model_id: e.target.value.trim() || null }),
        })),
        h('label', { class: 'field' }, '版本（revision）', h('input', {
          type: 'text', value: cfg.model_revision || '', placeholder: '固定版本',
          onchange: (e) => saveConfig({ model_revision: e.target.value.trim() || null }),
        }))),
      p.mode === 'lrc' ? h('div', { class: 'row' },
        numField('软锚点容差 σ (ms)', dec.soft_sigma_ms, (v) => saveConfig({ decode: { soft_sigma_ms: v } }), { min: 10 }),
        numField('先验强度 λ', dec.soft_lambda, (v) => saveConfig({ decode: { soft_lambda: v } }), { step: 0.5, min: 0 }),
        numField('窗口左边距 (ms)', dec.left_margin_ms, (v) => saveConfig({ decode: { left_margin_ms: v } }), { min: 0 }),
        numField('窗口右边距 (ms)', dec.right_margin_ms, (v) => saveConfig({ decode: { right_margin_ms: v } }), { min: 0 }),
        numField('硬锚点容差 (ms)', dec.hard_tolerance_ms, (v) => saveConfig({ decode: { hard_tolerance_ms: v } }), { min: 0 })) : null,
      h('div', { class: 'row' },
        h('label', {}, '尾音策略 ', h('select', { value: cfg.tail?.strategy || 'off', onchange: (e) => saveConfig({ tail: { strategy: e.target.value } }) },
          h('option', { value: 'off' }, '关闭（保留模型边界）'),
          h('option', { value: 'trim' }, '保守裁短'),
          h('option', { value: 'energy' }, '基于人声持续性修正'))),
        h('label', {}, h('input', { type: 'checkbox', checked: cfg.retry?.enabled !== false, onchange: (e) => saveConfig({ retry: { enabled: e.target.checked } }) }), '异常句有限重试'),
        h('span', { class: 'muted small' }, '尾音修正保留原值、方法与原因，不覆盖人工锁定。'))),
    section('运行',
      preflight.map(([k, m]) => h('div', { class: k === 'err' ? 'error-box' : 'notice' }, m)),
      h('div', { class: 'row' },
        h('button', {
          class: 'primary', disabled: preflight.some(([k]) => k === 'err') || jobs.some((j) => ['queued', 'running'].includes(j.status)),
          onclick: (e) => startAlign({
            busy: e.target,
            onResult: (rid) => { selectResult(rid); toast('对齐完成，可在第 6 步检查结果', 'ok'); emit('rerender'); },
          }),
        }, p.mode === 'lrc' ? '开始对齐（LRC 增强）' : '开始对齐（普通模式）'),
        h('span', { class: 'muted small' }, '人工锁定的时间会保留到新结果中；新结果不会覆盖人工修改。')),
      jobs.map((j) => h('div', { class: `job-detail ${j.status}` },
        h('div', { class: 'row' },
          h('strong', {}, j.label || '对齐'), badge(statusText(j), j.status === 'failed' ? 'err' : j.status === 'succeeded' ? 'ok' : ''),
          ['queued', 'running'].includes(j.status) ? h('button', { onclick: () => run(() => cancelJob(j.id)) }, '取消') : null),
        h('progress', { max: 1, value: j.progress || 0, class: 'wide' }),
        j.message ? h('div', { class: 'small muted' }, j.message) : null,
        j.error ? h('div', { class: 'error-box small' }, j.error) : null))),
    resultsActions(),
  );
}

function resultsActions() {
  const results = view()?.results || [];
  const card = resultsCard();
  card.append(h('div', { class: 'row' }, fileButton('导入 alignment.json…', '.json,application/json', async (file) => run(async () => {
    const res = await POST(ppath('/results/import'), { text: await readFileText(file) });
    setPV(res);
    if (res.result_id) selectResult(res.result_id);
    toast('已导入对齐结果（未设为当前结果；过期状态已重新计算）', 'ok');
  })), h('span', { class: 'muted small' }, '导入的结果与当前输入不一致时会标记为过期。')));
  if (!results.length) return card;
  card.append(h('div', { class: 'row' },
    h('span', {}, '选择结果：'),
    results.map((r) => h('span', { class: 'result-pick' },
      h('button', { class: r.id === S.resultId ? 'on' : '', onclick: () => { selectResult(r.id); S.step = 'review'; emit('rerender'); } }, `查看 ${r.created.slice(11, 19)}`),
      r.id !== project().active_result_id ? h('button', {
        class: 'small', onclick: () => run(async () => setPV(await POST(ppath(`/results/${r.id}/activate`)))),
      }, '设为当前') : null))));
  return card;
}
