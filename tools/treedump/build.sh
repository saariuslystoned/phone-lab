#!/bin/sh
set -e

# Locates the SDK from ANDROID_SDK_ROOT, ANDROID_HOME, or ~/Library/Android/sdk
SDK_ROOT=""
if [ -n "$ANDROID_SDK_ROOT" ] && [ -d "$ANDROID_SDK_ROOT" ]; then
    SDK_ROOT="$ANDROID_SDK_ROOT"
elif [ -n "$ANDROID_HOME" ] && [ -d "$ANDROID_HOME" ]; then
    SDK_ROOT="$ANDROID_HOME"
elif [ -d "$HOME/Library/Android/sdk" ]; then
    SDK_ROOT="$HOME/Library/Android/sdk"
else
    echo "Android SDK not found: set ANDROID_SDK_ROOT or ANDROID_HOME" >&2
    exit 1
fi

# Picks the highest platforms/android-*/android.jar
ANDROID_JAR=$(find "$SDK_ROOT/platforms" -name "android.jar" 2>/dev/null | sort -V 2>/dev/null | tail -n 1)
if [ -z "$ANDROID_JAR" ] || [ ! -f "$ANDROID_JAR" ]; then
    ANDROID_JAR=$(find "$SDK_ROOT/platforms" -name "android.jar" 2>/dev/null | sort | tail -n 1)
fi
if [ -z "$ANDROID_JAR" ] || [ ! -f "$ANDROID_JAR" ]; then
    echo "android.jar not found in $SDK_ROOT/platforms" >&2
    exit 1
fi

# Picks the highest build-tools/*/d8
D8=$(find "$SDK_ROOT/build-tools" -name "d8" 2>/dev/null | sort -V 2>/dev/null | tail -n 1)
if [ -z "$D8" ]; then
    D8=$(find "$SDK_ROOT/build-tools" -name "d8" 2>/dev/null | sort | tail -n 1)
fi
if [ -z "$D8" ] || [ ! -x "$D8" ]; then
    echo "d8 not found or not executable in $SDK_ROOT/build-tools" >&2
    exit 1
fi

if ! command -v javac >/dev/null 2>&1; then
    echo "javac not found on PATH" >&2
    exit 1
fi

if ! command -v zip >/dev/null 2>&1; then
    echo "zip not found on PATH" >&2
    exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

rm -rf build/classes build/dex build/treedump.jar
mkdir -p build/classes build/dex

javac --release 17 -cp "$ANDROID_JAR" -d build/classes src/lab/phone/treedump/*.java
"$D8" --release --lib "$ANDROID_JAR" --output build/dex build/classes/lab/phone/treedump/*.class
zip -j build/treedump.jar build/dex/classes.dex >/dev/null

SHA=""
if command -v sha256sum >/dev/null 2>&1; then
    SHA=$(sha256sum build/treedump.jar | awk '{print $1}')
elif command -v shasum >/dev/null 2>&1; then
    SHA=$(shasum -a 256 build/treedump.jar | awk '{print $1}')
else
    echo "neither sha256sum nor shasum found on PATH" >&2
    exit 1
fi

echo "tools/treedump/build/treedump.jar $SHA"
