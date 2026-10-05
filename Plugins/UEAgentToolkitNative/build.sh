#!/usr/bin/env bash
# Builds UEAgentToolkitNative for the installed engine and copies Binaries next to the .uplugin.
# Usage: ./build.sh   (env UE_ENGINE_DIR, default D:/UE/UE_5.8). Close the editor first.
set -e
ENGINE="${UE_ENGINE_DIR:-D:/UE/UE_5.8}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${BUILD_OUT:-$(dirname "$HERE")/../../_ATNBuild}"   # short path: avoids Windows MAX_PATH issues
"$ENGINE/Engine/Build/BatchFiles/RunUAT.bat" BuildPlugin -Plugin="$HERE/UEAgentToolkitNative.uplugin" \
  -Package="$OUT" -TargetPlatforms=Win64 -Rocket
rm -rf "$HERE/Binaries" && cp -r "$OUT/Binaries" "$HERE/" && rm -rf "$OUT"
echo "Built: $HERE/Binaries"
