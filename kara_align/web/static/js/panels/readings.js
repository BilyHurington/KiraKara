// Step 3: readings (rules / manual / AI) and optional vocal separation.

import { POST, PUT, run } from '../api.js';
import { S, emit, lines, ppath, setPV, view } from '../state.js';
import { badge, details, h, section, toast } from '../util.js';
import { renderAiBox } from './ai.js';
import { renderSeparationCard } from './audio.js';

const SOURCE_LABEL = { rule: '规则', manual: '人工', ai: 'AI', import: '导入', none: '无' };

export function renderEnhancePanel(panel) {
  panel.append(
    h('p', { class: 'muted' }, '注音、分离与校准互不等待，可按任意顺序进行；未分离也能听原曲并校准。'),
    readingsCard(),
    section('AI 注音往返', renderAiBox()),
    renderSeparationCard(),
  );
}

function readingsCard() {
  const ls = lines().filter((l) => l.sing && l.kind === 'lyric');
  const overwrite = h('input', { type: 'checkbox', checked: true });
  const warnings = view()?.capability_warnings || [];
  const report = S.prepareReport;
  return section('读音与发音单元',
    h('div', { class: 'row' },
      h('button', {
        class: 'primary', disabled: !ls.length,
        onclick: (e) => run(async () => {
          const r = await POST(ppath('/readings/prepare'), { overwrite_rule: overwrite.checked });
          S.prepareReport = r.report || null;
          setPV(r);
          toast('规则注音完成（人工与 AI 读音未被覆盖）', 'ok', 3000);
        }, { busy: e.target }),
      }, '规则注音'),
      h('label', { class: 'small' }, overwrite, '覆盖已有的规则读音（人工 / AI / 已确认读音永远保留）')),
    warnings.map((w) => h('div', { class: 'notice' }, w)),
    report ? details('注音报告', false, h('pre', { class: 'pre small' }, typeof report === 'string' ? report : JSON.stringify(report, null, 2))) : null,
    h('p', { class: 'muted small' },
      '日语按拍整理（拗音合并；促音、拨音、长音保留）。点击片段可修改读音；单元用 “/” 或空格分隔。',
      '颜色：', badge('人工', 'src-manual'), badge('AI', 'src-ai'), badge('规则', 'src-rule'), badge('不确定', 'warn')),
    ls.length ? h('div', { class: 'readings' }, ls.map(lineReadings)) : h('p', { class: 'muted' }, '尚无参与对齐的歌词行'));
}

function lineReadings(ln) {
  const editing = S.editSeg && S.editSeg.lineId === ln.id ? S.editSeg : null;
  return h('div', { class: 'reading-line' },
    h('div', { class: 'reading-text' }, ln.text),
    h('div', { class: 'chips' }, (ln.segments || []).map((seg) => segChip(ln, seg, editing))),
    !ln.segments?.length ? h('span', { class: 'muted small' }, '尚未生成发音单元（请运行规则注音或应用 AI 结果）') : null,
    editing ? segEditor(ln, ln.segments.find((s) => s.id === editing.segId)) : null);
}

function segChip(ln, seg, editing) {
  if (!seg.units?.length && !seg.reading) {
    return h('span', { class: 'chip plain' }, seg.surface);
  }
  const cls = ['chip', `src-${seg.reading_source}`];
  if (seg.uncertain) cls.push('uncertain');
  if (editing && editing.segId === seg.id) cls.push('editing');
  const title = [
    `来源：${SOURCE_LABEL[seg.reading_source] || seg.reading_source}`,
    seg.confirmed ? '已人工确认（锁定）' : '',
    seg.uncertain ? '读音不确定' : '',
    seg.candidates?.length ? `候选：${seg.candidates.join('、')}` : '',
    seg.lang !== 'ja' ? `语言：${seg.lang}` : '',
    seg.note || '',
  ].filter(Boolean).join('\n');
  return h('span', {
    class: cls.join(' '), title,
    onclick: () => { S.editSeg = { lineId: ln.id, segId: seg.id }; emit('rerender'); },
  },
  h('span', { class: 'surface' }, seg.surface),
  h('span', { class: 'units' }, (seg.units || []).map((u) => u.reading).join('/') || seg.reading || '?'),
  seg.confirmed ? h('span', { class: 'lock' }, '🔒') : null);
}

function segEditor(ln, seg) {
  if (!seg) return null;
  const reading = h('input', { type: 'text', value: seg.reading || (seg.units || []).map((u) => u.reading).join(''), placeholder: '读音（假名 / 拼音 / 单词）' });
  const units = h('input', { type: 'text', value: (seg.units || []).map((u) => u.reading).join('/'), placeholder: '单元，用 / 或空格分隔；留空则自动按拍拆分' });
  const confirm = h('input', { type: 'checkbox', checked: true });
  const save = (e) => run(async () => {
    const unitList = units.value.split(/[\/\s]+/).map((s) => s.trim()).filter(Boolean);
    const body = { reading: reading.value.trim(), confirm: confirm.checked };
    if (unitList.length) body.units = unitList;
    if (!body.reading) { toast('读音不能为空'); return; }
    setPV(await PUT(ppath(`/lines/${ln.id}/segments/${seg.id}`), body));
    S.editSeg = null;
  }, { busy: e.target });
  return h('div', { class: 'seg-editor' },
    h('div', { class: 'row' },
      h('strong', {}, seg.surface),
      h('label', {}, '读音 ', reading),
      h('label', {}, '单元 ', units),
      h('label', { class: 'small' }, confirm, '确认并锁定（AI/规则不再覆盖）')),
    seg.candidates?.length ? h('div', { class: 'row small' }, '候选：', seg.candidates.map((c) => h('button', {
      class: 'small', onclick: () => { reading.value = c; units.value = ''; },
    }, c))) : null,
    h('div', { class: 'row' },
      h('button', { class: 'primary', onclick: save }, '保存'),
      h('button', { onclick: () => { S.editSeg = null; emit('rerender'); } }, '取消'),
      h('span', { class: 'muted small' }, '分组变化会生成新的单元 ID，旧的人工时间不会误绑到新单元。')));
}
