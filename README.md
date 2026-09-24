# Kara Align

已知歌词的时间戳对齐工具。输入音频、歌词（可选 LRC），输出可复用的**逐发音单元时间**（原音频起点起算的整数毫秒，区间 `[start_ms, end_ms)`）。使用现成模型，不训练、不微调；不负责字体、布局、ASS 样式或特效。

- Python 核心（`kara_align/`），CLI（`kara-align`）与本地 WebUI（`kara-align serve`）共用同一套服务层与时间语义。
- 默认 CTC 对齐权重：[`NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn`](https://huggingface.co/NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn)，固定 revision `2ab2b5f4…`，**许可 CC-BY-NC-SA-4.0（非商用）**。
- 可选人声分离：[`python-audio-separator`](https://github.com/nomadkaraoke/python-audio-separator)（BS-RoFormer / MelBand RoFormer 等预设，许可请以上游为准）。

## 安装

```bash
uv venv --python 3.12 .venv
uv pip install -e ".[ml,dev]"          # 对齐模型（torch + transformers）
uv pip install -e ".[separation]"      # 可选：人声分离
```

需要 `ffmpeg`（解码 mp3/m4a 等；可用环境变量 `KARA_ALIGN_FFMPEG` 指定路径）。模型权重在第一次对齐时下载到 Hugging Face 缓存；歌词获取与模型下载只在用户主动操作时联网。

## WebUI

```bash
kara-align serve            # http://127.0.0.1:8765
```

流程：**选择模式 → 输入音频和歌词 → 可选 AI 注音／人声分离 → LRC 首音校准 → 对齐 → 人工检查 → 导出**。注音、分离、校准互不等待。

- 歌词：粘贴或上传（同一解析与预览流程）；网易云／QQ 音乐单曲链接、分享文案、短链、`netease:ID` / `qq:MID`；专辑／歌单先列出歌曲再选择。翻译／音译轨单独配对，默认只对齐原文。
- AI 注音：页面生成提示词（含行 ID、原文、已有读音、返回格式），复制到任意网页聊天，把返回的 JSON 粘贴回来；程序校验后预览再应用。不接入任何 LLM API。
- 音频：原曲可以是音频，也可以是视频（mp4 / mov / mkv 等，自动无损提取第一条音轨继续后续流程）；原曲、人声、伴奏、自定义混音同步试听，慢速试听时游标和标记仍是原音频时间。
- 导出：人声保留比例在“导出”页设置（播放器的“自定义混音”按同一设置试听）；以视频为原曲时可直接导出降低人声的视频（画面不重新编码，声音保持原有的音画偏移）。
- 校准：波形缩放、定位、局部循环、慢速，标记所选歌词的首个发音 → `user_shift = marked − base`（每次重新计算，不叠加）；可数值微调、确认零偏移、中段／末段检查、撤销。
- 检查：单元时间与异常提示，定位试听，数值或拖拽修改起止，锁定，撤销／重做，局部重跑（只生成候选结果，采用需手动确认）。
- 卡拉OK字幕：4 个即用预设；布局（靠顶/靠底、1–3 行、左右交替/居中、边距、行距、交替行向中间缩进）、歌词样式（字体、字号、颜色、描边、阴影、扫光方式）、注音（平假名/片假名/罗马音，仅汉字/全部，送假名自动分开，过宽时加宽歌词或允许超出），可在歌词下方显示翻译；任意时刻预览完整画面（与烧录同一 libass 渲染器，无视频时纯黑）；导出 ASS 或一键烧录成 MP4（原视频或纯黑背景，原声 / 降低人声 / 无声）。设计见 [`docs/karaoke.md`](docs/karaoke.md)。

## CLI 示例

```bash
kara-align init work/song --mode lrc
kara-align lyrics work/song song.lrc            # 或：cat song.lrc | kara-align lyrics work/song -
kara-align fetch "https://music.163.com/#/song?id=123" --project work/song --with-track translation
kara-align audio work/song song.flac
kara-align lines work/song --readings           # 查看规则注音（! = 不确定）
kara-align ai-prompt work/song --out prompt.txt # 粘贴到网页聊天
kara-align ai-apply work/song reply.txt         # 校验并应用注音补丁（--dry-run 仅预览）
kara-align separate work/song --preset bs-roformer   # 可选
kara-align calibrate work/song --mark L0001 12950    # 标记首音
kara-align calibrate work/song --check L0030 95120   # 中段检查
kara-align align work/song                      # --role vocals 使用人声；--lines L0005 局部重跑
kara-align show work/song
kara-align edit work/song <unit_id> --start 12950 --end 13120
kara-align export work/song alignment           # alignment / prepared / project / csv / lrc-line / lrc-unit / lrc-calibrated
kara-align mix work/song --vocal 20 --inst 100  # 人声保留 20% 的混音 WAV
kara-align video work/song --vocal 20           # 以视频为原曲时：导出降低人声的视频
kara-align export work/song karaoke-ass         # 卡拉OK字幕（样式在 WebUI 设置）
kara-align burn work/song --audio mix           # 烧录卡拉OK字幕视频（无视频时纯黑背景）
kara-align package work/song song.kara.zip      # 便携项目包
kara-align eval --ref ref.json --hyp base=a.json --hyp lrc=b.json   # 与人工标注比较
```

## 时间与数据约定

- 对外时间统一为原音频起点起算的整数毫秒；内部保留样本／帧坐标，只在输出时取整。每个后端自带帧→样本映射（`FrameMap`），不写死 20 ms。
- LRC `[offset]` 按惯例解释：正值表示歌词提前显示，导入时规范化一次为 `embedded_shift_ms = −offset`，原值保留。
  `base_i = imported_start_i + embedded_shift`，`effective_i = base_i + user_shift`；人工锁定的单行锚点是原音频绝对时间，不随全局平移移动。
- 导出的对齐结果已是绝对时间，不再加偏移；“校准后 LRC”直接写有效时间并清除 `[offset]`，重新导入不会重复移动。
- 失败或缺失的时间为 `null` 并附原因，不均分填充；声学分数只是归一化 log 概率，不是正确概率。
- 三个独立对象：歌词文档（Line → Segment → Unit）、音频资产（原曲／人声／伴奏，内容指纹与原点映射）、对齐结果（模型原始预测与人工覆盖分开保存，记录模型 revision、转写 profile、配置与输入快照）。

## 算法概要

- 声学：wav2vec2 CTC 逐帧 `logp[t, token]`，长音频按固定分块 + 上下文推理，只取中心帧，按卷积步长精确拼接；结果按（音频指纹、模型、revision、分块配置）缓存，改读音／锚点／模式只重新解码。
- 解码：带时间先验的 CTC Viterbi。普通模式整段有序对齐；LRC 增强模式按 `W_i = [effective_i − left, next_anchor + right]` 定位局部窗口，紧邻句联合对齐只提交目标句。软锚点代价 `−λ·Huber((t − anchor)/σ)` 仅在首次进入句首 token 时计一次；硬锚点在允许区间外为 −∞。
- 检查与重试：覆盖率、异常短／长区间、句首偏差、窗口边缘拥挤、跨句冲突、上下文稳定性、单元内部长停顿（读音可能不符）；有限预算的候选重试（放宽容差、联合邻句、切换原曲／人声、已确认的读音候选），明显分歧的候选保留供人工试听。
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
