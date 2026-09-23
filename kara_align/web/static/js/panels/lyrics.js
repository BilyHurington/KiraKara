// Step 2: lyrics input (paste / upload / music link), preview, track pairing, line editing.

import { PATCH, POST, PUT, run } from '../api.js';
import { positionMs } from '../player.js';
import { S, emit, lines, ppath, project, setPV, view } from '../state.js';
import { badge, details, fileButton, fmtMs, h, readFileText, section, timeInput, toast } from '../util.js';
import { renderAiBox } from './ai.js';
import { renderAudioCard } from './audio.js';

S.draft ||= { lyrics: '', link: '', track: '', ai: '' };
S.selLines ||= new Set();

const KIND_LABELS = { lyric: '歌词', translation: '翻译', romanization: '音译', meta: '信息', blank: '空行' };

export function renderInputPanel(panel) {
  panel.append(
    renderAudioCard(),
    lyricsInputCard(),
    S.lyricsPreview ? previewCard() : null,
    linkCard(),
    lineTableCard(),
    trackCard(),
    section('AI 注音往返（可选）', renderAiBox()),
  );
}

// ------------------------------------------------------------ paste / upload

function lyricsInputCard() {
  const ta = h('textarea', {
    id: 'lyrics-input', rows: 8, placeholder: '粘贴歌词、LRC、注音 JSON（prepared.json）或项目 JSON…',
    value: S.draft.lyrics, oninput: (e) => { S.draft.lyrics = e.target.value; },
  });
  const mode = project().mode;
  return section('歌词输入',
    h('p', { class: 'muted small' }, mode === 'lrc'
      ? '当前为 LRC 增强模式：需要带行时间的歌词。'
      : '当前为普通模式：导入 LRC 时只取正文，时间会被忽略。'),
    ta,
    h('div', { class: 'row' },
      h('button', { class: 'primary', onclick: (e) => parse(S.draft.lyrics, 'paste', null, e.target) }, '解析并预览'),
      fileButton('上传文件…', '.lrc,.txt,.json,text/plain,application/json', async (file) => {
        const text = await readFileText(file);
        S.draft.lyrics = text;
        await parse(text, 'upload', file.name);
      }),
      h('span', { class: 'muted small' }, '粘贴与上传共用同一解析与预览；解析失败不会覆盖当前项目。')));
}

async function parse(text, origin, filename, busy) {
  if (!text.trim()) {
    toast('歌词为空');
    return;
  }
  S.lastParse = { text, origin, filename };
  const pv = await run(() => POST(ppath('/lyrics/parse'), { text, origin, filename }), { busy });
  if (pv) {
    S.lyricsPreview = pv;
    emit('rerender');
  }
}

function previewCard() {
  const pv = S.lyricsPreview;
  const doc = pv.doc;
  const blocked = !!pv.error;
  const hasLyrics = lines().length > 0;
  const rows = (doc?.lines || []).map((ln, i) => h('tr', { class: ln.sing ? '' : 'dim' },
    h('td', {}, i + 1),
    h('td', { class: 'mono' }, ln.imported_start_ms != null ? fmtMs(ln.imported_start_ms) : '—'),
    h('td', {}, KIND_LABELS[ln.kind] || ln.kind),
    h('td', {}, ln.sing ? '✓' : '—'),
    h('td', {}, ln.text, ln.translation ? h('div', { class: 'muted small' }, ln.translation) : null)));
  const extra = pv.extra_tracks && Object.keys(pv.extra_tracks).length ? pv.extra_tracks : null;
  return section('解析预览',
    h('div', { class: 'row' },
      badge(`格式：${pv.detected}`),
      badge(`${doc?.lines?.length || 0} 行`),
      doc?.embedded_offset_raw != null ? badge(`内嵌 offset：${doc.embedded_offset_raw} → 平移 ${doc.embedded_shift_ms} ms`, 'info') : null,
      doc?.meta?.title ? badge(`${doc.meta.title}${doc.meta.artist ? ' — ' + doc.meta.artist : ''}`) : null),
    doc?.embedded_offset_note ? h('p', { class: 'muted small' }, doc.embedded_offset_note) : null,
    (pv.warnings || []).map((w) => h('div', { class: 'notice' }, w)),
    blocked ? h('div', { class: 'error-box' },
      h('div', {}, pv.error),
      h('div', { class: 'row' },
        h('button', { onclick: () => { document.getElementById('lyrics-input')?.focus(); toast('请在输入框中为歌词补充 [mm:ss.xx] 行时间后重新解析', 'info'); } }, '补充时间'),
        h('button', {
          onclick: (e) => run(async () => {
            setPV(await PATCH(ppath(), { mode: 'plain' }));
            if (S.lastParse) await parse(S.lastParse.text, S.lastParse.origin, S.lastParse.filename);
          }, { busy: e.target }),
        }, '切换到普通模式'))) : null,
    pv.route ? routeBox(pv.route) : null,
    extra ? h('p', { class: 'muted small' }, `该歌曲还提供：${Object.keys(extra).map((k) => KIND_LABELS[k] || k).join('、')}（应用后可在下方“翻译/音译配对”中使用）`) : null,
    h('div', { class: 'scroll-box' }, h('table', { class: 'grid' },
      h('thead', {}, h('tr', {}, ['#', '时间', '类型', '演唱', '文本'].map((t) => h('th', {}, t)))),
      h('tbody', {}, rows))),
    h('div', { class: 'row' },
      h('button', {
        class: 'primary', disabled: blocked,
        onclick: (e) => run(async () => {
          const res = await POST(ppath('/lyrics/apply'), { preview_id: pv.preview_id });
          S.extraTracks = pv.extra_tracks || S.extraTracks;
          S.lyricsPreview = null;
          setPV(res);
          toast('歌词已应用', 'ok', 2500);
        }, { busy: e.target }),
      }, '应用'),
      h('button', { onclick: () => { S.lyricsPreview = null; emit('rerender'); } }, '放弃'),
      hasLyrics ? h('span', { class: 'muted small' }, '应用会替换当前歌词文档；旧对齐结果保留但会标记为过期。') : null));
}

/** The pasted text is a project / alignment / reading patch: offer the right place for it. */
function routeBox(route) {
  const text = S.lastParse?.text || '';
  const r = String(route).toLowerCase();
  const actions = [];
  if (r === 'json-project' || r === 'project') {
    actions.push(h('button', {
      onclick: (e) => run(async () => {
        const fd = new FormData();
        fd.append('file', new Blob([text], { type: 'application/json' }), S.lastParse?.filename || 'project.json');
        const pv = await POST('/api/projects/import', fd);
        S.lyricsPreview = null;
        setPV(pv);
        toast(`已作为项目导入：${pv.project.name}`, 'ok');
      }, { busy: e.target }),
    }, '作为项目导入'));
  }
  if (r === 'json-alignment' || r === 'alignment') {
    actions.push(h('button', {
      onclick: (e) => run(async () => {
        const res = await POST(ppath('/results/import'), { text });
        S.lyricsPreview = null;
        setPV(res);
        toast('已导入对齐结果（未设为当前结果）', 'ok');
      }, { busy: e.target }),
    }, '作为对齐结果导入'));
  }
  if (r === 'json-reading-patch' || r === 'reading-patch') {
    actions.push(h('button', {
      onclick: () => { S.draft.ai = text; S.lyricsPreview = null; emit('rerender'); toast('已放入下方“粘贴 AI 结果”框，请点击“校验并预览”', 'info'); },
    }, '作为 AI 注音结果使用'));
  }
  return h('div', { class: 'row' }, h('span', { class: 'small' }, `该文件应导入到：${route}`), actions);
}

// ------------------------------------------------------------ music link

function linkCard() {
  const input = h('input', {
    type: 'text', class: 'wide', value: S.draft.link,
    placeholder: '网易云 / QQ 音乐单曲链接、分享文案、短链，或 netease:123 / qq:songmid',
    oninput: (e) => { S.draft.link = e.target.value; },
  });
  return section('音乐链接获取歌词',
    h('p', { class: 'muted small' }, '只获取歌词与必要元数据，不下载音频；专辑/歌单会先列出歌曲供选择。需要联网，由你主动触发。'),
    h('div', { class: 'row' }, input,
      h('button', {
        onclick: (e) => run(async () => {
          const r = await POST('/api/lyrics/link', { text: S.draft.link });
          S.song = r.kind === 'song' ? r.song : null;
          S.collection = r.kind === 'collection' ? r : null;
          emit('rerender');
        }, { busy: e.target }),
      }, '获取')),
    S.collection ? collectionView() : null,
    S.song ? songView(S.song) : null);
}

function collectionView() {
  const c = S.collection;
  return h('div', { class: 'scroll-box' },
    h('p', {}, `共 ${c.songs.length} 首，请选择一首：`),
    h('table', { class: 'grid' },
      h('tbody', {}, c.songs.map((s) => h('tr', {},
        h('td', {}, s.title), h('td', {}, (s.artists || []).join(' / ')), h('td', {}, s.album || ''),
        h('td', { class: 'mono' }, s.duration_ms ? fmtMs(s.duration_ms) : ''),
        h('td', {}, h('button', {
          onclick: (e) => run(async () => {
            const r = await POST('/api/lyrics/song', { platform: s.platform || c.platform, song_id: s.song_id });
            S.song = r.song;
            emit('rerender');
          }, { busy: e.target }),
        }, '选择')))))));
}

function songView(song) {
  const trackRow = (k) => {
    const has = !!song.tracks?.[k];
    const timed = song.has_timestamps?.[k];
    return h('li', {}, `${KIND_LABELS[k] || (k === 'original' ? '原文' : k)}：`,
      has ? (timed ? badge('带时间', 'ok') : badge('无时间（不会伪造 LRC）', 'warn')) : badge('无', ''));
  };
  return h('div', { class: 'song-card' },
    h('h4', {}, song.title || '(无标题)'),
    h('div', {}, `歌手：${(song.artists || []).join(' / ') || '—'}　专辑：${song.album || '—'}　时长：${song.duration_ms ? fmtMs(song.duration_ms) : '—'}　平台：${song.platform} #${song.song_id}`),
    h('ul', {}, ['original', 'translation', 'romanization'].map(trackRow)),
    song.tracks?.original ? details('查看原文歌词', false, h('pre', { class: 'pre' }, song.tracks.original)) : null,
    h('div', { class: 'row' },
      h('button', {
        class: 'primary', disabled: !song.tracks?.original,
        onclick: (e) => run(async () => {
          const pv = await POST(ppath('/lyrics/from-song'), { platform: song.platform, song_id: song.song_id });
          S.lyricsPreview = pv;
          S.extraTracks = pv.extra_tracks || null;
          S.lastParse = { text: song.tracks.original, origin: song.platform, filename: null };
          emit('rerender');
        }, { busy: e.target }),
      }, '使用此歌曲的原文轨（预览）'),
      h('button', { onclick: () => { S.song = null; emit('rerender'); } }, '关闭')));
}

// ------------------------------------------------------------ line table

function lineTableCard() {
  const ls = lines();
  if (!ls.length) return section('歌词行', h('p', { class: 'muted' }, '尚未应用歌词'));
  const eff = view()?.effective_starts || {};
  const lrc = project().mode === 'lrc';
  const caret = {};
  const rows = ls.map((ln, i) => {
    const textInput = h('input', {
      type: 'text', class: 'wide', value: ln.text,
      onchange: (e) => run(async () => setPV(await PATCH(ppath(`/lines/${ln.id}`), { text: e.target.value }))),
      onkeyup: (e) => { caret[ln.id] = e.target.selectionStart; },
      onclick: (e) => { caret[ln.id] = e.target.selectionStart; },
    });
    const anchor = ln.anchor;
    const hardBox = h('input', { type: 'checkbox', checked: anchor ? anchor.hard : true, title: '硬锚点（人工确认，允许模型时间网格量化）' });
    const putAnchor = (abs) => run(async () => setPV(await PUT(ppath(`/lines/${ln.id}/anchor`), {
      abs_ms: abs, hard: hardBox.checked, tolerance_ms: anchor?.tolerance_ms ?? 80,
    })));
    hardBox.addEventListener('change', () => anchor && putAnchor(anchor.abs_ms));
    return h('tr', { class: (ln.sing ? '' : 'dim') + (S.selLines.has(ln.id) ? ' selected' : '') },
      h('td', {}, h('input', {
        type: 'checkbox', checked: S.selLines.has(ln.id),
        onchange: (e) => { if (e.target.checked) S.selLines.add(ln.id); else S.selLines.delete(ln.id); },
      })),
      h('td', {}, i + 1),
      h('td', { class: 'mono small' }, ln.imported_start_ms != null ? fmtMs(ln.imported_start_ms) : '—',
        lrc && eff[ln.id] ? h('div', { class: 'muted' }, `有效 ${fmtMs(eff[ln.id].ms)}`) : null),
      h('td', {}, h('select', {
        value: ln.kind,
        onchange: (e) => run(async () => setPV(await PATCH(ppath(`/lines/${ln.id}`), { kind: e.target.value }))),
      }, Object.entries(KIND_LABELS).map(([k, v]) => h('option', { value: k }, v)))),
      h('td', {}, h('input', {
        type: 'checkbox', checked: ln.sing, title: '参与对齐',
        onchange: (e) => run(async () => setPV(await PATCH(ppath(`/lines/${ln.id}`), { sing: e.target.checked }))),
      })),
      h('td', {}, textInput,
        ln.translation ? h('div', { class: 'muted small' }, `译：${ln.translation}`) : null,
        ln.romanization ? h('div', { class: 'muted small' }, `音：${ln.romanization}`) : null,
        ln.source?.merged_from?.length ? h('div', { class: 'muted small' }, `合并自 ${ln.source.merged_from.length} 行`) : null,
        ln.source?.split_from ? h('div', { class: 'muted small' }, '由拆分产生（无锚点的子行不分配时间）') : null),
      h('td', { class: 'nowrap' },
        timeInput(anchor?.abs_ms ?? null, (v) => putAnchor(v), { title: '人工锁定的单行绝对锚点（原音频时间，不随全局平移）' }),
        h('button', { class: 'small', title: '用播放头位置作为锚点', onclick: () => putAnchor(Math.round(positionMs())) }, '⌖'),
        hardBox),
      h('td', {}, h('button', {
        class: 'small', title: '在文本框光标处拆分',
        onclick: () => {
          const at = caret[ln.id] ?? textInput.selectionStart;
          if (!at || at >= ln.text.length) { toast('请先把光标放到要拆分的位置'); return; }
          run(async () => setPV(await POST(ppath(`/lines/${ln.id}/split`), { at })));
        },
      }, '拆分')));
  });
  return section('歌词行（可编辑）',
    h('div', { class: 'row' },
      h('button', {
        onclick: () => {
          const ids = ls.filter((l) => S.selLines.has(l.id)).map((l) => l.id);
          if (ids.length < 2) { toast('请至少勾选两行相邻的行'); return; }
          run(async () => { setPV(await POST(ppath('/lines/merge'), { line_ids: ids })); S.selLines.clear(); });
        },
      }, '合并所选行'),
      h('button', { onclick: () => { S.selLines.clear(); emit('rerender'); } }, '清除选择'),
      h('span', { class: 'muted small' }, '重复副歌保留为独立行实例；合并/拆分保留来源，拆分出的子行不会被平均分配时间。')),
    h('div', { class: 'scroll-box tall' }, h('table', { class: 'grid lines' },
      h('thead', {}, h('tr', {}, ['', '#', '导入时间', '类型', '演唱', '文本', '单行锚点（绝对 ms / 硬）', ''].map((t) => h('th', {}, t)))),
      h('tbody', {}, rows))));
}

// ------------------------------------------------------------ translation / romanization pairing

function trackCard() {
  if (!lines().length) return null;
  S.trackKind ||= 'translation';
  const ta = h('textarea', {
    rows: 5, value: S.draft.track, placeholder: '粘贴翻译或音译（LRC 或纯文本）',
    oninput: (e) => { S.draft.track = e.target.value; },
  });
  const preview = async (text, origin, filename, busy) => {
    const r = await run(() => POST(ppath('/lyrics/track/preview'), { text, kind: S.trackKind, origin, filename }), { busy });
    if (r) { S.trackPreview = { ...r, kind: S.trackKind }; emit('rerender'); }
  };
  const extra = S.extraTracks || {};
  return section('翻译 / 音译配对（可选，不参与对齐）',
    h('div', { class: 'row' },
      h('select', { value: S.trackKind, onchange: (e) => { S.trackKind = e.target.value; } },
        h('option', { value: 'translation' }, '翻译'), h('option', { value: 'romanization' }, '音译')),
      Object.entries(extra).filter(([, t]) => t).map(([k, t]) => h('button', {
        onclick: () => { S.draft.track = t; S.trackKind = k === 'romanization' ? 'romanization' : 'translation'; emit('rerender'); },
      }, `使用获取的${KIND_LABELS[k] || k}`))),
    ta,
    h('div', { class: 'row' },
      h('button', { onclick: (e) => preview(S.draft.track, 'paste', null, e.target) }, '预览配对'),
      fileButton('上传文件…', '.lrc,.txt,text/plain', async (file) => {
        const text = await readFileText(file);
        S.draft.track = text;
        await preview(text, 'upload', file.name);
      })),
    S.trackPreview ? trackPreviewView() : null);
}

function trackPreviewView() {
  const tp = S.trackPreview;
  const byLine = new Map(tp.pairs.map((p) => [p.line_id, p]));
  const options = [...new Set([...tp.pairs.map((p) => p.text), ...(tp.unmatched || [])].filter(Boolean))];
  const chosen = new Map(tp.pairs.map((p) => [p.line_id, p.text]));
  const rows = lines().filter((l) => l.kind === 'lyric').map((ln) => {
    const p = byLine.get(ln.id);
    const sel = h('select', { class: 'wide', value: p?.text || '', onchange: (e) => chosen.set(ln.id, e.target.value) },
      h('option', { value: '' }, '（不配对）'), options.map((o) => h('option', { value: o }, o)));
    return h('tr', {}, h('td', {}, ln.text), h('td', {}, sel), h('td', { class: 'muted small' }, p?.method || ''));
  });
  return h('div', {},
    tp.unmatched?.length ? h('div', { class: 'notice' }, `${tp.unmatched.length} 条未匹配，可在下拉框中手动指定`) : null,
    h('div', { class: 'scroll-box' }, h('table', { class: 'grid' },
      h('thead', {}, h('tr', {}, h('th', {}, '原文行'), h('th', {}, tp.kind === 'translation' ? '翻译' : '音译'), h('th', {}, '配对方式'))),
      h('tbody', {}, rows))),
    h('div', { class: 'row' },
      h('button', {
        class: 'primary',
        onclick: (e) => run(async () => {
          const pairs = [...chosen.entries()].map(([line_id, text]) => ({ line_id, text }));
          setPV(await POST(ppath('/lyrics/track/apply'), { kind: tp.kind, pairs }));
          S.trackPreview = null;
          toast('已应用配对', 'ok', 2500);
        }, { busy: e.target }),
      }, '应用配对'),
      h('button', { onclick: () => { S.trackPreview = null; emit('rerender'); } }, '放弃')));
}
