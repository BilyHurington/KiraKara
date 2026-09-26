# Kara Align

已知歌词的时间戳对齐工具。输入音频、歌词（可选 LRC），输出可复用的**逐发音单元时间**（原音频起点起算的整数毫秒，区间 `[start_ms, end_ms)`），并可以用对齐结果生成带排版、注音、翻译与特效的卡拉OK字幕（ASS），一键烧录成视频。使用现成模型，不训练、不微调。

- Python 核心（`kara_align/`），CLI（`kara-align`）与本地 WebUI（`kara-align serve`）共用同一套服务层与时间语义。
- 默认 CTC 对齐权重：[`NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn`](https://huggingface.co/NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn)，固定 revision `2ab2b5f4…`，**许可 CC-BY-NC-SA-4.0（非商用）**。
- 可选人声分离：[`python-audio-separator`](https://github.com/nomadkaraoke/python-audio-separator)（BS-RoFormer / MelBand RoFormer 等预设，许可请以上游为准）。

## 安装

```bash
uv venv --python 3.12 .venv
uv pip install -e ".[ml,dev]"          # 对齐模型（torch + transformers）
uv pip install -e ".[separation]"      # 可选：人声分离
```

需要 `ffmpeg`（解码 mp3/m4a 等；可用环境变量 `KARA_ALIGN_FFMPEG` 指定路径）。模型权重在第一次对齐时下载到 Hugging Face 缓存。音频、视频和项目文件只保存在本机；只有用户主动操作时才联网：下载模型、从音乐链接获取歌词（网易云 / QQ 音乐），以及 AI 注音（歌词会发送给所选的 AI 服务）。

## WebUI

```bash
kara-align serve            # http://127.0.0.1:8765
```

服务只接受发往本机名（`127.0.0.1` / `localhost` / `[::1]`）的请求，其他主机名一律 403；在局域网中用本机地址访问时加 `--allow-host <地址>`。其他网站的页面不能修改数据（带外站 `Origin` 的非 GET 请求 403）。同一个项目目录只由一个服务进程运行极简模式的任务队列，再开第二个进程时它只能查看任务。页面连不上本地服务时顶部会一直显示提示，可点“重试”。

**极简模式**（默认）：设置好之后只需选模式、放入视频、粘贴音乐链接（网易云 / QQ 音乐）或歌词，点“开始制作”；LRC 模式下几秒后请你确认一次第一句从哪里开始唱，之后任务排队自动完成注音、人声分离、对齐并生成卡拉OK视频。完成的任务点开进入详细模式继续调整。设计见 [`docs/simple-mode.md`](docs/simple-mode.md)。

**详细模式**流程：**选择模式 → 输入音频和歌词 → 可选 AI 注音／人声分离 → LRC 首音校准 → 对齐 → 人工检查 → 卡拉OK字幕（可选） → 导出**。注音、分离、校准互不等待。

- 歌词：粘贴或上传（同一解析与预览流程）；网易云／QQ 音乐单曲链接、分享文案、短链、`netease:ID` / `qq:MID`；专辑／歌单先列出歌曲再选择。翻译／音译轨单独配对，默认只对齐原文。
- AI 注音：一键交给本机已登录的 Claude Code / Codex 命令行，或任意 OpenAI 兼容 API（设置里选择）；也可以复制提示词到任意网页聊天，把返回的 JSON 粘贴回来。回复都经过同样的校验，预览后再应用。
- 音频：原曲可以是音频，也可以是视频（mp4 / mov / mkv 等，自动无损提取第一条音轨继续后续流程）；播放器可在原曲、人声、伴奏之间切换，同步试听，慢速试听时游标和标记仍是原音频时间。换了原曲之后，旧原曲分离出的人声 / 伴奏标为“需重新分离”，在此之前不用于对齐、试听或混音。
- 导出：人声保留比例在“导出”页设置，“试听此混音”用播放器按同一规则试听；以视频为原曲时可直接导出降低人声的视频（画面不重新编码，声音保持原有的音画偏移）。“最近导出”列出项目 `exports` 文件夹里的视频、混音等文件（重启服务后仍在）。导出所用的结果已过期或有行因在音频结束之后而被跳过时，导出会给出提示。
- 校准：波形缩放、定位、局部循环、慢速，标记所选歌词的首个发音 → `user_shift = marked − base`（每次重新计算，不叠加）；可数值微调、确认零偏移、中段／末段检查、“撤销校准”。导入另一份歌词会取消已确认的校准；原来的偏移属于另一份歌词时重置为 0（可撤销）。
- 检查：单元时间与异常提示，定位试听，数值或拖拽修改起止，锁定，撤销／重做（⌘Z / Ctrl+Z，⇧ 重做；只撤销人工检查里的时间修改，不撤销校准），局部重跑（只生成候选结果，采用需手动确认；在“对齐”页点局部结果的“查看”，也可以在人工检查的结果选择框里直接查看）。
- 快捷键（详细模式）：Space 播放 / 暂停，L 开关循环，首音校准页 M 在播放位置标记，人工检查里 ↑/↓ 选择单元、Enter 播放。焦点在按钮、单选、滑块等控件上时 Space / Enter / 方向键归控件自己；对话框打开时或输入法正在输入时不触发快捷键；按住不放不会重复触发（↑/↓ 除外）。循环打开时跳到循环区间外（点击波形、输入时间等）会关闭循环，区间保留，按 L 重新打开。取消正在进行的操作需要再确认一次。
- 卡拉OK字幕：可保存 / 切换的样式预设（内置“默认”“暖阳”）；开头的歌曲信息标题卡（歌名 / 歌手 / 专辑 / 作词作曲，可自定义文字）；独立的配色面板；淡入淡出；荧光边缘；跟着演唱逐字出现的字幕特效（光晕扩散、光环爆开、闪光扫过、星光迸发、花瓣飘落、爱心飘升、跳跃小球）；翻译字幕（单独样式）；布局（靠顶/靠底、1–3 行、左右交替/居中、边距、行距、交替行向中间缩进）、歌词样式（字体、字号、颜色、描边、阴影、扫光方式）、注音（平假名/片假名/罗马音，仅汉字/全部，送假名自动分开，过宽时加宽歌词或允许超出），可在歌词下方显示翻译；任意时刻预览完整画面（与烧录同一 libass 渲染器、同样的画面尺寸，无视频时纯黑）；导出 ASS 或一键烧录成 MP4（原视频或纯黑背景，原声 / 降低人声 / 无声）。字幕、预览、ASS 和视频总是使用项目的当前对齐结果（在人工检查里看的是别的结果时页面会说明）。设计见 [`docs/karaoke.md`](docs/karaoke.md)。

## CLI 示例

```bash
kara-align init work/song --mode lrc
kara-align lyrics work/song song.lrc            # 或：cat song.lrc | kara-align lyrics work/song -
kara-align fetch "https://music.163.com/#/song?id=123" --project work/song --with-track translation
kara-align audio work/song song.flac
kara-align lines work/song --readings           # 查看规则注音（! = 不确定）
kara-align readings work/song                   # 重新生成规则注音（--no-overwrite-rule 保留现有规则读音；人工/AI/已确认读音都不覆盖）
kara-align ai-prompt work/song --out prompt.txt # 粘贴到网页聊天
kara-align ai-apply work/song reply.txt         # 校验并应用注音补丁（--dry-run 仅预览）
kara-align separate work/song --preset bs-roformer   # 可选；预设 melband-roformer（默认）/ bs-roformer / mdx-fast / demucs-htdemucs
kara-align calibrate work/song --mark L0001 12950    # 标记首音
kara-align calibrate work/song --check L0030 95120   # 中段检查
kara-align align work/song                      # --role vocals 使用人声；--lines L0005 局部重跑
kara-align show work/song
kara-align edit work/song <unit_id> --start 12950 --end 13120
kara-align export work/song alignment           # alignment / prepared / project / csv / lrc-line / lrc-unit / lrc-calibrated
kara-align mix work/song --vocal 20 --inst 100  # 人声保留 20% 的混音 WAV
kara-align video work/song --vocal 20           # 以视频为原曲时：导出降低人声的视频
kara-align export work/song karaoke-ass         # 卡拉OK字幕（样式在 WebUI 设置）
kara-align burn work/song --audio mix --vocal 20   # 烧录卡拉OK字幕视频（无视频时纯黑背景；--vocal 缺省用字幕样式里的人声保留比例）
kara-align package work/song song.kara.zip      # 便携项目包
kara-align eval --ref ref.json --hyp base=a.json --hyp lrc=b.json   # 与人工标注比较
kara-align serve --allow-host 192.168.1.20      # 另外接受这个主机名的请求（可重复）
```

## 时间与数据约定

- 对外时间统一为原音频起点起算的整数毫秒；内部保留样本／帧坐标，只在输出时取整。每个后端自带帧→样本映射（`FrameMap`），不写死 20 ms。
- LRC `[offset]` 按惯例解释：正值表示歌词提前显示，导入时规范化一次为 `embedded_shift_ms = −offset`，原值保留。
  `base_i = imported_start_i + embedded_shift`，`effective_i = base_i + user_shift`；人工锁定的单行锚点是原音频绝对时间，不随全局平移移动。
  不是有限数字或绝对值超过 1 小时的 `[offset]` 忽略并提示。时间标签接受最多 6 位小数（超过毫秒的部分四舍五入）；秒数 ≥ 60 的标签（`[00:75.00]`）按实际秒数换算并提示。增强 LRC 的逐字标签不作为单元时间，但行尾后面没有文字的逐字标签记为该行的结束时间。
- LRC 导入：同一时间标签下一行带假名、一行不带假名（中文或拉丁字母）的配对至少有两处时，文件被认作双语歌词，不带假名的一行成为翻译；此后同一时间的两行都没有假名时（英文行、全汉字行），另一行是中文或与歌词不同的文字也作为翻译；两行都带假名（对唱）保持为两行歌词；QQ 音乐表示“没有翻译”的 `//` 行永远不作为翻译。“作词：…”“Mixed by: …”等署名行（标签只由署名用词组成）标为信息行不参与演唱；“词”“曲”“鼓”“Mix”这类也常出现在歌词里的单字 / 单词只在第一句演唱之前或 00:00 处才算署名；网易云开头 00:00 的“歌名 - 歌手”行也是信息行。
- 导出的对齐结果已是绝对时间，不再加偏移；“校准后 LRC”直接写有效时间并清除 `[offset]`，原 LRC 里的每个带时间的行（包括不演唱的行、署名行和句尾空行标记）都按校准后的时间写出，没有时间的行原样写出，重新导入不会重复移动。
- 失败或缺失的时间为 `null` 并附原因，不均分填充；声学分数只是归一化 log 概率，不是正确概率。
- 三个独立对象：歌词文档（Line → Segment → Unit）、音频资产（原曲／人声／伴奏，内容指纹与原点映射）、对齐结果（模型原始预测与人工覆盖分开保存，记录模型 revision、转写 profile、配置与输入快照）。

## 算法概要

- 注音：日语按词切分（MeCab / fugashi + UniDic lite）：汉字词连同送假名为一个片段（好き、始まり），助词单独，助动词和接续助词跟随前一个词（なってく、咲いて）；规则读音按上下文取词典读音（君→きみ，は／へ作助词时读わ／え），另附 pykakasi 读音作候选。未安装 MeCab 时退回按字符类别切分。旧项目点“规则注音”会把按字符类别切出的片段按词重新分组（好|き → 好き），读音和发音单元（及其时间）不变；AI 提示词也要求按词切分、熟字训整体一段。
- 声学：wav2vec2 CTC 逐帧 `logp[t, token]`，长音频按固定分块 + 上下文推理，只取中心帧，按卷积步长精确拼接；结果按（音频指纹、模型、revision、分块配置）缓存，改读音／锚点／模式只重新解码。
- 长音与促音：`ー`、`〜`、`～`（以及紧跟假名的 ASCII `~`，如 `ラララ~`）都读作长音 `ー`，是单独的发音单元。`っ` / `ー` 只取同一行内的相邻音：行末的 `っ` 没有可重复的辅音、行首的 `ー` 没有可延长的元音，都保持未对齐，不借用上一行 / 下一行。
- 解码：带时间先验的 CTC Viterbi。普通模式整段有序对齐（局部重跑只解码该行前后相邻行时间之间的一段）；LRC 增强模式按 `W_i = [effective_i − left, next_anchor + right]` 定位局部窗口，紧邻句联合对齐只提交目标句；第一句带时间的行之前的无时间行，窗口从音频开头开始。LRC 时间在音频结束之后的行（例如剪短的视频）不对齐，也不作为其他行的上下文，结果里给出提示。软锚点代价 `−λ·Huber((t − anchor)/σ)` 仅在首次进入句首 token 时计一次；硬锚点在允许区间外为 −∞。
- 间奏保护：一行之内的停顿按秒计代价（行与行之间的停顿不计），有人声分轨时再按分轨能量判断哪里没在唱（相对全曲演唱电平的软阈值，能容忍间奏里的乐器漏音），在没唱的地方放字或在行内停顿都要额外付代价，所以句尾的字不会被扔进间奏。LRC 里句后带时间的空行被当作句尾标记：该侧不再联合邻句，搜索窗口最多到标记后 6 秒。卡拉OK字幕里若一行中间仍有长停顿，停顿期间暂时隐藏该行。
- 检查与重试：覆盖率、异常短／长区间、句首偏差、窗口边缘拥挤、跨句冲突、上下文稳定性、单元内部长停顿（读音可能不符）、行内长停顿、单元落在人声分轨无声处；有限预算的候选重试（放宽容差、联合邻句、切换原曲／人声、已确认的读音候选），句首偏差相差不到 150 ms 的候选视为同等（软锚点没有这么准），不会因此替换原结果；明显分歧的候选保留供人工试听。没有模型 token 的单元（行末的 `っ`、模型拼不出的字符）只提示一次，不触发重试；有文字（例如数字 3人）却没有读音的片段不参与对齐，提示 `segment_no_reading`。
- 人工修改：重新对齐时，上一结果中的人工时间先套用再做检查；原曲换过时不套用（保留在修改历史中，提示 `manual_audio_changed`）；片段读音改了但单元数相同时按位置跟随（`manual_reading_changed`），无法对应的人工时间逐条列出（`manual_dropped`），不会悄悄丢失。锁定没有时间的单元会被拒绝。
- 尾音：关闭（默认）／保守裁短／基于人声能量的有限修正，记录原值、方法与原因，不覆盖人工锁。
- 人声混音：`mix = master × (p/100·V + q/100·I)`，防削波使用可见的共同母线增益，试听与导出同一规则。

## 开发

```bash
.venv/bin/pytest            # 默认不跑需要网络 / 模型权重的测试
.venv/bin/pytest -m ml      # 需要已下载的模型
```

HTTP 接口见 [`docs/api.md`](docs/api.md)。

### WebUI 前端

源码在 `frontend/`（Vite + React + TypeScript + Tailwind），构建产物提交在 `kara_align/web/static/`，因此只使用 Python 时不需要 Node。修改前端后：

```bash
cd frontend
npm install
npm run dev        # 开发：http://localhost:5173，/api 代理到 127.0.0.1:8799（KARA_API_PORT 可改）
npm run build      # 类型检查并输出到 kara_align/web/static
```

开发时另开一个终端运行 `kara-align serve --port 8799`。
