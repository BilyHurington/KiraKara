// Step 7: exports (all formats with loss warnings), stems, mix, project package.

import { GET, POST, run } from '../api.js';
import { roleLabel } from '../player.js';
import { S, emit, ppath, project, selectResult, setPV, view } from '../state.js';
import { badge, copyText, fileButton, h, readFileText, section, toast } from '../util.js';

const NEEDS_RESULT = new Set(['alignment', 'csv', 'lrc-line', 'lrc-unit']);

export function renderExportPanel(panel) {
  const p = project();
  const formats = S.info?.export_formats || {};
  const results = view()?.results || [];
  S.exportResultId ||= S.resultId;
  if (!results.some((r) => r.id === S.exportResultId)) S.exportResultId = S.resultId;
  const rsum = results.find((r) => r.id === S.exportResultId);
  const q = (fmt, extra = '') => {
    const params = new URLSearchParams();
    if (NEEDS_RESULT.has(fmt) && S.exportResultId) params.set('result_id', S.exportResultId);
    return ppath(`/export/${fmt}?${params.toString()}${extra}`);
  };

  const rows = Object.entries(formats).map(([fmt, meta]) => {
    const needs = NEEDS_RESULT.has(fmt);
    const disabled = needs && !S.exportResultId;
    if (fmt === 'lrc-calibrated' && p.mode !== 'lrc') {
      return h('tr', { class: 'dim' }, h('td', {}, meta.description), h('td', { class: 'mono small' }, meta.filename),
        h('td', { colspan: 2, class: 'muted small' }, '仅 LRC 增强模式可用'));
    }
    return h('tr', {},
      h('td', {}, meta.description),
      h('td', { class: 'mono small' }, meta.filename),
      h('td', { class: 'nowrap' },
        disabled ? h('button', { disabled: true }, '下载') : h('a', { class: 'button', href: q(fmt, '&download=1'), download: meta.filename }, '下载'),
        h('button', { disabled, onclick: (e) => preview(fmt, q(fmt), e.target, true) }, '复制'),
        h('button', { disabled, onclick: (e) => preview(fmt, q(fmt), e.target, false) }, '预览')),
      h('td', { class: 'small' }, (S.exportWarnings?.[fmt] || []).map((w) => h('div', { class: 'warn-text' }, w))));
  });

  const assets = (p.audio || []).filter((a) => view()?.audio?.[a.role]?.available);
  panel.append(
    section('导出',
      h('div', { class: 'row' },
        h('label', {}, '对齐结果 ', h('select', { value: S.exportResultId || '', onchange: (e) => { S.exportResultId = e.target.value; emit('rerender'); } },
          results.length ? results.map((r) => h('option', { value: r.id }, `${r.created}${r.id === p.active_result_id ? ' [当前]' : ''}${r.coverage?.full ? '' : ' [局部]'}${r.stale ? ' [过期]' : ''}`))
            : h('option', { value: '' }, '（暂无结果）'))),
        rsum?.stale ? badge(`已过期：${rsum.stale_reason || ''}`, 'warn') : null,
        rsum && !rsum.coverage?.full ? badge('局部结果：导出标明覆盖范围', 'warn') : null),
      h('p', { class: 'muted small' },
        'alignment.json 为完整标准输出；其他格式可能丢失读音映射、终点、间隙或失败信息，会在下方提示。',
        '“校准后 LRC”只包含整体平移后的原始行锚点，并清除已应用的 offset，重新导入不会重复移动；“对齐后 LRC”由模型结果聚合。'),
      h('table', { class: 'grid' },
        h('thead', {}, h('tr', {}, ['格式', '文件', '', '损失提示'].map((t) => h('th', {}, t)))),
        h('tbody', {}, rows)),
      S.exportPreview ? h('div', {},
        h('h4', {}, `预览：${S.exportPreview.filename}`),
        h('textarea', { rows: 14, readonly: true, class: 'mono small wide', value: S.exportPreview.content }),
        h('button', { onclick: () => { S.exportPreview = null; emit('rerender'); } }, '关闭预览')) : null),
    section('音频',
      assets.length ? h('ul', {}, assets.map((a) => h('li', {},
        h('a', { href: ppath(`/audio/${a.id}/playback.wav`), download: `${roleLabel(a.role)}-${a.sha256.slice(0, 8)}.wav` }, `${roleLabel(a.role)} WAV`),
        h('span', { class: 'muted small' }, ` · ${a.source?.kind || ''}${a.source?.model ? ' · ' + a.source.model : ''}`)))) : h('p', { class: 'muted' }, '暂无音频'),
      h('p', { class: 'muted small' }, '混音 WAV 可在底部播放器中“导出混音 WAV”（与试听使用相同混音规则，正常速度、原始时长与原点；监听音量不写入）。'),
      S.lastMixExport ? h('div', {}, h('a', { href: S.lastMixExport.url, download: S.lastMixExport.filename }, `下载上次导出的混音：${S.lastMixExport.filename}`),
        S.lastMixExport.report ? h('pre', { class: 'pre small' }, JSON.stringify(S.lastMixExport.report, null, 2)) : null) : null),
    importResultCard(),
    section('项目',
      h('div', { class: 'row' },
        h('a', { class: 'button', href: ppath('/package?include_audio=1'), download: '' }, '下载项目包（含音频）'),
        h('a', { class: 'button', href: ppath('/package?include_audio=0'), download: '' }, '下载项目包（仅文本，音频按内容关联）'),
        h('a', { class: 'button', href: ppath('/export/project?download=1'), download: 'project.json' }, 'project.json')),
      h('p', { class: 'muted small' }, '项目文件自包含歌词、校准、AI 往返与人工修改；不包含模型权重、密钥或本机路径。缺失音频可在打开后重新上传。')),
  );
}

function importResultCard() {
  S.draft ||= {};
  const ta = h('textarea', {
    rows: 4, class: 'mono small', value: S.draft.resultImport || '', placeholder: '粘贴 alignment.json 内容',
    oninput: (e) => { S.draft.resultImport = e.target.value; },
  });
  const doImport = (text, busy) => run(async () => {
    if (!text.trim()) { toast('内容为空'); return; }
    const res = await POST(ppath('/results/import'), { text });
    S.draft.resultImport = '';
    setPV(res);
    if (res.result_id) { selectResult(res.result_id); S.exportResultId = res.result_id; }
    toast('已导入对齐结果（未设为当前结果；过期状态已重新计算）', 'ok');
  }, { busy });
  return section('导入结果',
    h('p', { class: 'muted small' }, '导入之前导出的 alignment.json 作为非当前结果；与当前输入不一致时会标记为过期。'),
    ta,
    h('div', { class: 'row' },
      h('button', { onclick: (e) => doImport(S.draft.resultImport || '', e.target) }, '导入粘贴内容'),
      fileButton('上传 alignment.json…', '.json,application/json', async (file) => doImport(await readFileText(file)))));
}

async function preview(fmt, url, busy, copy) {
  const r = await run(() => GET(url), { busy });
  if (!r) return;
  S.exportWarnings ||= {};
  S.exportWarnings[fmt] = r.warnings || [];
  if (copy) {
    if (await copyText(r.content)) toast(`${r.filename} 已复制`, 'ok', 2000);
    else { toast('自动复制失败，已显示预览，请手动复制', 'info'); S.exportPreview = r; }
  } else {
    S.exportPreview = r;
  }
  emit('rerender');
}
