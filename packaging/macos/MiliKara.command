#!/bin/bash
# MiliKara 启动脚本（macOS 离线版）
#   双击：启动 WebUI 并在浏览器中打开；关闭这个终端窗口（或按 Ctrl+C）即停止。
#   ./MiliKara.command <命令> ...：运行命令行工具，例如 ./MiliKara.command --help
APP="$(cd "$(dirname "$0")" && pwd)"

# 从网上下载的压缩包里的文件带有“隔离”标记：第一次放行本脚本后，把整个目录的标记一并去掉
xattr -dr com.apple.quarantine "$APP" 2>/dev/null

PY="$APP/python/bin/python3"
export KARA_ALIGN_MODELS="$APP/models"          # 自带的模型
export KARA_ALIGN_FONTS="$APP/fonts"            # 自带的字体（Noto Sans CJK）
export KARA_ALIGN_FFMPEG="$APP/ffmpeg/bin/ffmpeg"
export PATH="$APP/ffmpeg/bin:$PATH"              # ffprobe、fc-list / fc-match 以及分离组件用到的 ffmpeg
export FONTCONFIG_FILE="$APP/ffmpeg/etc/fonts/fonts.conf"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1  # 对齐模型只从自带目录加载
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONHOME PYTHONPATH VIRTUAL_ENV
# 设置、字幕预设和项目默认在 ~/.kara_align（KARA_ALIGN_HOME 可以改）

if [ $# -gt 0 ]; then
  exec "$PY" -m kara_align.cli "$@"
fi

cd "$APP" || exit 1
echo "MiliKara 正在启动（第一次启动需要十几秒；关闭这个窗口即停止 MiliKara）"
OPEN=--open
[ -n "$MILIKARA_NO_BROWSER$KIRAKARA_NO_BROWSER" ] && OPEN=  # (KIRAKARA_: the name before v1.1.0)
exec "$PY" -m kara_align.cli serve --port auto $OPEN
