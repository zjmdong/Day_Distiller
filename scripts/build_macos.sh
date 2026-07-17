#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "The macOS bundle must be compiled on macOS." >&2
  exit 2
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Apple Silicon arm64 is required; refusing to create an Intel-only build." >&2
  exit 3
fi

PYTHON="${DAY_DISTILLER_PYTHON:-python3}"
FFMPEG="$($PYTHON -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$(command -v ffmpeg)")"
FFPROBE="$($PYTHON -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$(command -v ffprobe)")"
ICON="$ROOT/assets/day-distiller-icon.png"
BUILD="$ROOT/build/nuitka/macos-arm64"
DIST="$ROOT/dist"
PACKAGE="$DIST/DayDistiller-macOS-AppleSilicon.zip"

mkdir -p "$BUILD" "$DIST"
"$PYTHON" -m nuitka \
  --mode=app \
  --macos-target-arch=arm64 \
  --macos-app-name="Day Distiller" \
  --macos-app-version=0.3.0 \
  --macos-signed-app-name=com.daydistiller.desktop \
  --macos-app-icon="$ICON" \
  --enable-plugin=pyside6 \
  --assume-yes-for-downloads \
  --lto=no \
  --python-flag=no_docstrings \
  --noinclude-pytest-mode=nofollow \
  --noinclude-setuptools-mode=nofollow \
  --include-data-files="$ICON=assets/day-distiller-icon.png" \
  --include-data-files="$FFMPEG=resources/ffmpeg/ffmpeg" \
  --include-data-files="$FFPROBE=resources/ffmpeg/ffprobe" \
  --output-dir="$BUILD" \
  --report="$BUILD/nuitka-report.xml" \
  "$ROOT/scripts/nuitka_entry.py"

APP="$BUILD/nuitka_entry.app"
if [[ ! -d "$APP" ]]; then
  echo "Nuitka app bundle was not found: $APP" >&2
  exit 4
fi
EXECUTABLE="$APP/Contents/MacOS/Day Distiller"
if [[ ! -f "$EXECUTABLE" ]]; then
  EXECUTABLE="$(find "$APP/Contents/MacOS" -maxdepth 1 -type f -perm -111 | head -n 1)"
fi
file "$EXECUTABLE" | grep -q "arm64" || {
  echo "Compiled executable is not arm64: $EXECUTABLE" >&2
  exit 5
}
codesign --force --deep --sign - "$APP"
rm -f "$PACKAGE"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$PACKAGE"
echo "Built native Apple Silicon package: $PACKAGE"
