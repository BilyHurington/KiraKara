# The newest release build (GPL, static, with libass/fontconfig) of BtbN/FFmpeg-Builds into <Dest>:
# bin\ffmpeg.exe, bin\ffprobe.exe, LICENSE.txt and README-ffmpeg.txt.  Needs gh (GH_TOKEN).
param([Parameter(Mandatory)] [string] $Dest)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$names = gh api repos/BtbN/FFmpeg-Builds/releases/tags/latest --jq '.assets[].name'
$name = $names | Where-Object { $_ -match '^ffmpeg-n(\d+\.\d+)-latest-win64-gpl-\d+\.\d+\.zip$' } |
  Sort-Object { [version]($_ -replace '^ffmpeg-n(\d+\.\d+).*$', '$1') } | Select-Object -Last 1
if (-not $name) { throw "no win64-gpl release build in BtbN/FFmpeg-Builds" }
Write-Host "ffmpeg: $name"

$tmp = Join-Path $env:RUNNER_TEMP "ffmpeg-dl"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp
New-Item -ItemType Directory -Force $tmp | Out-Null
gh release download latest --repo BtbN/FFmpeg-Builds --pattern $name --dir $tmp
Expand-Archive (Join-Path $tmp $name) -DestinationPath $tmp
$src = Get-ChildItem $tmp -Directory | Select-Object -First 1

New-Item -ItemType Directory -Force "$Dest\bin" | Out-Null
Copy-Item "$($src.FullName)\bin\ffmpeg.exe", "$($src.FullName)\bin\ffprobe.exe" "$Dest\bin"
Copy-Item "$($src.FullName)\LICENSE.txt" "$Dest\LICENSE.txt"
$readme = @(
  "ffmpeg (GPL v3, static build with libass / fontconfig / freetype / harfbuzz; no nonfree parts)"
  "Source: https://github.com/BtbN/FFmpeg-Builds ($name), https://ffmpeg.org/download.html"
  ""
) + (& "$Dest\bin\ffmpeg.exe" -hide_banner -version)
$readme | Out-File -Encoding utf8 "$Dest\README-ffmpeg.txt"
& "$Dest\bin\ffmpeg.exe" -hide_banner -filters | Select-String -Pattern ' subtitles ' | ForEach-Object { Write-Host $_ }
Remove-Item -Recurse -Force $tmp
