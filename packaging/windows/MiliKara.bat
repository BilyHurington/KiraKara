@echo off
rem MiliKara launcher (portable Windows build).
rem   Double-click: starts the WebUI and opens it in the browser; close this window to stop.
rem   MiliKara.bat <command> ...: runs the command line tool, e.g.  MiliKara.bat --help
rem (ASCII only: cmd.exe reads batch files in the console code page.)
setlocal
set "APP=%~dp0"
set "KARA_ALIGN_MODELS=%APP%models"
set "KARA_ALIGN_FONTS=%APP%fonts"
set "KARA_ALIGN_FFMPEG=%APP%ffmpeg\bin\ffmpeg.exe"
set "PATH=%APP%ffmpeg\bin;%PATH%"
set "FONTCONFIG_FILE=%APP%ffmpeg\etc\fonts\fonts.conf"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "PYTHONNOUSERSITE=1"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONHOME="
set "PYTHONPATH="
set "VIRTUAL_ENV="
set "PY=%APP%python\python.exe"

if not "%~1"=="" goto cli

title MiliKara
set "OPEN=--open"
if defined MILIKARA_NO_BROWSER set "OPEN="
if defined KIRAKARA_NO_BROWSER set "OPEN="
"%PY%" -m kara_align.cli serve --port auto %OPEN%
if errorlevel 1 pause
exit /b

:cli
"%PY%" -m kara_align.cli %*
exit /b %errorlevel%
