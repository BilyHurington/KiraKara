// Audio assets: upload original, import stems (with sync report), separation.

import { POST, run } from '../api.js';
import { roleLabel } from '../player.js';
import { S, isJobRunning, ppath, project, refreshProject, setPV, trackJob, view } from '../state.js';
import { badge, details, fileButton, fmtMs, h, section, toast } from '../util.js';

const AUDIO_ACCEPT = 'audio/*,.wav,.flac,.mp3,.m4a,.aac,.ogg,.opus';

/** Multipart upload with progress (XHR); resolves to the ProjectView. */
function upload(file, role, onProgress) {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('role', role);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', ppath('/audio'));
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* not json */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(`${(data && data.detail) || xhr.statusText}（HTTP ${xhr.status}）`));
    };
    xhr.onerror = () => reject(new Error('上传失败：无法连接本地服务'));
    xhr.send(fd);
  });
}

function uploadButton(label, role) {
  const status = h('span', { class: 'small muted' });
  const btn = fileButton(label, AUDIO_ACCEPT, (file) => run(async () => {
    status.textContent = '上传中… 0%';
    try {
      const pv = await upload(file, role, (f) => { status.textContent = `上传中… ${Math.round(f * 100)}%`; });
      status.textContent = '';
      setPV(pv);
      toast(`${roleLabel(role)}已导入：${file.name}`, 'ok', 3000);
    } catch (e) {
      status.textContent = '';
      throw e;
    }
  }));
  return h('span', {}, btn, status);
}

export function renderAudioCard() {
  const p = project();
  const assets = p.audio || [];
  const rows = ['original', 'vocals', 'instrumental'].map((role) => {
    const a = assets.find((x) => x.role === role);
    const avail = view()?.audio?.[role]?.available;
    return h('tr', {},
      h('td', {}, roleLabel(role)),
      h('td', {}, a ? [
        h('div', {}, a.source?.filename || a.id, ' ', !avail ? badge('文件缺失，请重新上传同一音频', 'warn') : null),
        h('div', { class: 'muted small' },
          `${fmtMs(a.duration_ms)} · ${a.sample_rate} Hz · ${a.channels} 声道 · sha256 ${a.sha256.slice(0, 12)}… · 来源 ${a.source?.kind || ''}`,
          a.source?.model ? ` · 模型 ${a.source.model}` : '',
          a.origin_offset_samples ? ` · 原点偏移 ${a.origin_offset_samples} 样本` : ''),
        a.sync_report ? syncReport(a.sync_report) : null,
        (a.source?.notes || []).map((n) => h('div', { class: 'muted small' }, n)),
      ] : h('span', { class: 'muted' }, '未导入')),
      h('td', {}, uploadButton(a ? '替换…' : (role === 'original' ? '上传原曲…' : '导入…'), role)));
  });
  return section('音频',
    h('table', { class: 'grid' }, h('tbody', {}, rows)),
    h('p', { class: 'muted small' },
      '音频不会被移动或剪掉前奏。外部人声/伴奏会检查时间原点与同步（相同时长 ≠ 同步）。',
      '对齐可显式选择原曲或未衰减的人声。'));
}

function syncReport(r) {
  const ok = r.ok;
  return details(ok ? '同步检查：通过' : '同步检查：存在问题', !ok,
    h('div', { class: ok ? 'small' : 'notice small' },
      r.message ? h('div', {}, r.message) : null,
      h('div', { class: 'mono small' }, Object.entries(r)
        .filter(([k]) => k !== 'message')
        .map(([k, v]) => `${k}: ${typeof v === 'number' ? Number(v.toFixed?.(4) ?? v) : JSON.stringify(v)}`).join(' · '))));
}

export function renderSeparationCard() {
  const info = S.info || {};
  const presets = info.separation_presets || [];
  const available = info.separation_available;
  const hasOriginal = view()?.audio?.original?.available;
  S.sepPreset ||= presets[0]?.name || '';
  const running = isJobRunning('separate');
  return section('人声分离（可选）',
    h('p', { class: 'muted small' },
      '分离不保证对齐更准；异常句可以切回原曲比较。分离失败不会无声回退。分离输出保留原始时间轴。'),
    !available ? h('div', { class: 'notice' }, '未安装分离组件：pip install "kara-align[separation]"。也可在“音频”中导入已有的人声/伴奏。') : null,
    h('div', { class: 'row' },
      h('select', { value: S.sepPreset, onchange: (e) => { S.sepPreset = e.target.value; }, disabled: !presets.length },
        presets.map((p) => h('option', { value: p.name, title: p.notes || '' }, `${p.name}${p.filename ? ` (${p.filename})` : ''}`))),
      h('button', {
        class: 'primary', disabled: !available || !hasOriginal || running,
        onclick: (e) => run(async () => {
          const job = await POST(ppath('/separate'), { preset: S.sepPreset });
          trackJob(job, { label: '人声分离', onDone: () => refreshProject() });
        }, { busy: e.target }),
      }, running ? '分离中…' : '开始分离'),
      !hasOriginal ? h('span', { class: 'muted small' }, '需要先上传原曲') : null),
    presets.find((p) => p.name === S.sepPreset)?.notes ? h('p', { class: 'muted small' }, presets.find((p) => p.name === S.sepPreset).notes) : null);
}
