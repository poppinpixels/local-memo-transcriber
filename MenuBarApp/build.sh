#!/bin/bash
# Build Memo Transcriber menu bar app.
# Creates a self-contained .app bundle in the build/ directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG="${1:-release}"
APP_NAME="Memo Transcriber"
BUNDLE_DIR="build/${APP_NAME}.app"

echo "Building MemoTranscriber ($CONFIG)..."
swift build -c "$CONFIG" 2>&1

# Locate the built binary
BIN_PATH=".build/$CONFIG/MemoTranscriber"
if [ ! -f "$BIN_PATH" ]; then
    echo "Error: binary not found at $BIN_PATH" >&2
    exit 1
fi

# Create .app bundle
echo "Creating ${APP_NAME}.app..."
rm -rf "$BUNDLE_DIR"
mkdir -p "$BUNDLE_DIR/Contents/MacOS"
mkdir -p "$BUNDLE_DIR/Contents/Resources"

cp "$BIN_PATH" "$BUNDLE_DIR/Contents/MacOS/Memo Transcriber"
cp MemoTranscriber/Info.plist "$BUNDLE_DIR/Contents/"

# Write PkgInfo
printf 'APPL????' > "$BUNDLE_DIR/Contents/PkgInfo"

# Resolve Info.plist variables (minimal — matches the build settings)
/usr/libexec/PlistBuddy -c "Set :CFBundleExecutable 'Memo Transcriber'" "$BUNDLE_DIR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier 'com.poppinpixels.MemoTranscriber'" "$BUNDLE_DIR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName 'Memo Transcriber'" "$BUNDLE_DIR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundlePackageType 'APPL'" "$BUNDLE_DIR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString '1.0'" "$BUNDLE_DIR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion '1'" "$BUNDLE_DIR/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion '14.0'" "$BUNDLE_DIR/Contents/Info.plist"

echo ""
echo "Done: $BUNDLE_DIR"
echo ""
echo "To install:"
echo "  cp -r \"$BUNDLE_DIR\" /Applications/"
echo ""
echo "To run directly:"
echo "  open \"$BUNDLE_DIR\""
