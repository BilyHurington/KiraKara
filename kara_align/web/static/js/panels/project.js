// Header project bar, jobs bar and step 1 (mode selection).

import { PATCH, POST, run } from '../api.js';
import { S, cancelJob, emit, loadProjects, openProject, ppath, project, setPV, view } from '../state.js';
import { badge, clear, fileButton, fmtMs, h, section, toast } from '../util.js';

export function renderProjectBar() {
  const box = clear(document.getElementById('project-bar'));
  const sel = h('select', {
    onchange: (e) => e.target.value && run(() => openProject(e.target.value)),
    value: S.pid || '',
    title: '打开项目',
  },
  h('option', { value: '' }, S.projects.length ? '— 打开项目 —' : '— 暂无项目 —'),
  S.projects.map((p) => h('option', { value: p.id }, `${p.name}（${p.mode === 'lrc' ? 'LRC 增强' : '普通'}）`)));

  const importBtn = fileButton('导入项目', '.json,.zip,application/json,application/zip', (file) => run(async () => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const pv = await POST('/api/projects/import', fd);
    await loadProjects();
    setPV(pv);
    toast(`已导入项目：${pv.project.name}`, 'ok');
  }));

  box.append(
    sel,
    h('button', { onclick: () => { S.step = 'mode'; S.pid = null; S.pv = null; S.result = null; emit('project'); } }, '新建项目'),
    importBtn,
    ...(S.pid ? [h('a', { class: 'button', href: ppath('/package?include_audio=1'), download: '', title: '下载项目包（含音频，便于迁移与分享）' }, '下载项目包')] : []),
  );
}

export function renderJobsBar() {
  const box = clear(document.getElementById('jobs-bar'));
  for (const j of Object.values(S.jobs)) {
    const active = j.status === 'queued' || j.status === 'running';
    box.append(h('div', { class: `job ${j.status}`, title: j.error || j.message || '' },
      h('span', {}, j.label || j.kind),
      h('progress', { max: 1, value: j.progress || 0 }),
      h('span', { class: 'small' }, statusText(j)),
      active ? h('button', { class: 'link', onclick: () => run(() => cancelJob(j.id)) }, '取消') : null));
  }
}

export function statusText(j) {
  return {
    queued: '排队中', running: `${Math.round((j.progress || 0) * 100)}% ${j.message || ''}`,
    succeeded: '完成', failed: `失败：${j.error || ''}`, cancelled: '已取消',
  }[j.status] || j.status;
}

/** Step 1: create project / choose mode */
export function renderModePanel(panel) {
  const p = project();
  if (!p) {
    panel.append(newProjectCard(), projectListCard());
    return;
  }
  const mode = p.mode;
  const card = (m, title, body) => h('div', {
    class: `mode-card ${mode === m ? 'selected' : ''}`,
    onclick: () => mode !== m && run(async () => setPV(await PATCH(ppath(), { mode: m }))),
  }, h('h4', {}, mode === m ? '● ' : '○ ', title), ...body);

  panel.append(
    section('项目',
      h('div', { class: 'row' },
        h('label', {}, '名称 ', h('input', {
          type: 'text', value: p.name,
          onchange: (e) => run(async () => { setPV(await PATCH(ppath(), { name: e.target.value })); await loadProjects(); }),
        })),
        h('span', { class: 'muted small' }, `创建于 ${p.created} · 更新于 ${p.updated}`))),
    section('第一步：选择是否使用 LRC 增强',
      h('div', { class: 'mode-cards' },
        card('plain', '普通模式', [
          h('p', {}, '音频 + 已知歌词（或注音 JSON）。不使用任何外部时间锚点，对整段歌词做有序对齐。'),
          h('p', { class: 'muted' }, '导入 LRC 时只取正文，时间会被忽略（会明确提示）。'),
        ]),
        card('lrc', 'LRC 增强模式', [
          h('p', {}, '音频 + 带行时间的歌词。先校准全局偏移（标记所选歌词的首个发音），再用句首锚点约束细对齐。'),
          h('p', { class: 'muted' }, '没有有效时间时会要求补充时间或主动切换模式，不会静默降级。'),
        ])),
      view()?.mode_notice ? h('div', { class: 'notice' }, view().mode_notice) : null,
      h('p', { class: 'muted small' }, '切换模式会保留所有输入与人工修改；旧结果若不再适用于当前设置，会被标记为“已过期”（仍可查看）。')),
    resultsCard(),
  );
}

export function resultsCard() {
  const results = view()?.results || [];
  if (!results.length) return section('对齐结果', h('p', { class: 'muted' }, '暂无结果'));
  return section('对齐结果',
    h('table', { class: 'grid' },
      h('thead', {}, h('tr', {}, ['创建时间', '模式', '覆盖', '单元', '失败', '问题', '人工', '状态'].map((t) => h('th', {}, t)))),
      h('tbody', {}, results.map((r) => h('tr', {},
        h('td', {}, r.created, r.id === project().active_result_id ? badge('当前', 'ok') : null),
        h('td', {}, r.mode === 'lrc' ? 'LRC 增强' : '普通'),
        h('td', {}, r.coverage?.full ? '全曲' : `局部（${(r.coverage?.line_ids || []).length} 行）`),
        h('td', {}, r.n_units), h('td', {}, r.n_failed), h('td', {}, r.n_issues), h('td', {}, r.n_manual),
        h('td', {}, r.stale ? badge(`已过期：${r.stale_reason || ''}`, 'warn') : badge('有效', 'ok')))))));
}

function newProjectCard() {
  const name = h('input', { type: 'text', placeholder: '歌曲名', value: '' });
  let mode = 'plain';
  const radios = h('div', { class: 'row' },
    ['plain', 'lrc'].map((m) => h('label', {},
      h('input', { type: 'radio', name: 'newmode', value: m, checked: m === mode, onchange: () => { mode = m; } }),
      m === 'plain' ? '普通模式（不使用 LRC 时间）' : 'LRC 增强模式（使用行时间锚点）')));
  return section('新建项目',
    h('div', { class: 'row' }, h('label', {}, '名称 ', name)),
    radios,
    h('button', {
      class: 'primary',
      onclick: (e) => run(async () => {
        const pv = await POST('/api/projects', { name: name.value.trim() || '未命名', mode });
        await loadProjects();
        S.step = 'input';
        setPV(pv);
      }, { busy: e.target }),
    }, '创建'));
}

function projectListCard() {
  if (!S.projects.length) return section('已有项目', h('p', { class: 'muted' }, '暂无项目，可新建或导入 project.json / .kara.zip'));
  return section('已有项目',
    h('table', { class: 'grid' },
      h('thead', {}, h('tr', {}, h('th', {}, '名称'), h('th', {}, '模式'), h('th', {}, '更新'), h('th', {}, ''))),
      h('tbody', {}, S.projects.map((p) => h('tr', {},
        h('td', {}, p.name), h('td', {}, p.mode === 'lrc' ? 'LRC 增强' : '普通'), h('td', {}, p.updated),
        h('td', {}, h('button', { onclick: () => run(() => openProject(p.id)) }, '打开')))))));
}

export { fmtMs };
