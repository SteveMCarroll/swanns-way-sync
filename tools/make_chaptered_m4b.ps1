# Embed named landmark chapters into a copy of the audiobook (no re-encode).
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$src = Join-Path $root "Swann's Way.m4b"
$meta = Join-Path $root "out\Swanns_Way_chapters.ffmetadata"
$dst = Join-Path $root "out\Swann's Way (chaptered).m4b"

if (-not (Test-Path $src))  { throw "Missing audiobook: $src" }
if (-not (Test-Path $meta)) { throw "Missing chapters metadata: $meta (run build_correspondence.py)" }

ffmpeg -y -i $src -i $meta -map_metadata 1 -map_chapters 1 -c copy $dst
Write-Host "Wrote $dst"
