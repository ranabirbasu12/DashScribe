#!/usr/bin/env bash
# Build DashScribe.app bundle.
# Usage: ./build_app.sh
set -euo pipefail

# Use python.org-based venv for portable builds (macOS 14+ compatible).
# Falls back to ./venv if venv_build doesn't exist.
if [ -d "./venv_build" ]; then
    VENV="./venv_build"
else
    VENV="./venv"
fi

export MACOSX_DEPLOYMENT_TARGET=14.0

PYTHON="$VENV/bin/python"
BUNDLE="dist/DashScribe.app"
RESOURCES="$BUNDLE/Contents/Resources"
SITE_PKGS="$VENV/lib/python3.12/site-packages"

# Read version from version.py (single source of truth)
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' version.py)
echo "=== DashScribe v${VERSION} ==="

echo "=== Cleaning previous build ==="
rm -rf build/ dist/

echo "=== Building with py2app (venv: $VENV) ==="
$PYTHON setup.py py2app 2>&1 | tail -5

echo "=== Fixing namespace packages ==="
# mlx is a namespace package (no __init__.py) — py2app can't handle it.
# Remove partial mlx entries from zip, use full copy instead.
$PYTHON -c "
import zipfile, os
zip_path = '$RESOURCES/lib/python312.zip'
tmp_path = zip_path + '.tmp'
with zipfile.ZipFile(zip_path, 'r') as zin:
    with zipfile.ZipFile(tmp_path, 'w') as zout:
        for item in zin.infolist():
            if item.filename.startswith('mlx/'):
                continue
            zout.writestr(item, zin.read(item.filename))
os.replace(tmp_path, zip_path)
print('  Removed mlx/ from zip')
"

# Copy full mlx package
cp -R "$SITE_PKGS/mlx/" "$RESOURCES/lib/python3.12/mlx/"
touch "$RESOURCES/lib/python3.12/mlx/__init__.py"
# Remove py2app's partial core.so (keep original cpython-named one)
rm -f "$RESOURCES/lib/python3.12/lib-dynload/mlx/core.so" 2>/dev/null
rm -rf "$RESOURCES/lib/python3.12/lib-dynload/mlx" 2>/dev/null
echo "  Copied mlx namespace package"

# Copy PyObjCTools namespace package
cp -R "$SITE_PKGS/PyObjCTools/" "$RESOURCES/lib/python3.12/PyObjCTools/"
echo "  Copied PyObjCTools namespace package"

# Copy google namespace package (google.protobuf — needed by sentencepiece)
if [ -d "$SITE_PKGS/google" ]; then
    cp -R "$SITE_PKGS/google/" "$RESOURCES/lib/python3.12/google/"
    touch "$RESOURCES/lib/python3.12/google/__init__.py"
    echo "  Copied google namespace package (protobuf)"
fi

echo "=== Code signing ==="
CERT_NAME="DashScribe Developer"
if security find-identity -v -p codesigning 2>/dev/null | grep -q "\"${CERT_NAME}\""; then
    echo "  Signing with '${CERT_NAME}' (stable identity — permissions persist across rebuilds)"
    codesign --force --deep --sign "$CERT_NAME" "$BUNDLE"
else
    echo "  No '${CERT_NAME}' certificate found — using ad-hoc signing."
    echo "  TIP: Run ./setup_signing.sh once to create a stable signing identity."
    echo "       This lets macOS permissions survive across rebuilds."
    codesign --force --deep --sign - "$BUNDLE"
fi
xattr -cr "$BUNDLE"

echo "=== Creating packages for distribution ==="
DMG="dist/DashScribe.dmg"
ZIP="dist/DashScribe-${VERSION}.zip"
rm -f "$DMG" dist/DashScribe-*.zip

# Always create .zip (required for auto-updater)
echo "  Creating ZIP for auto-updater..."
ditto -c -k --sequesterRsrc --keepParent "$BUNDLE" "$ZIP"

# Compute SHA256 for integrity verification
SHA256=$(shasum -a 256 "$ZIP" | cut -d' ' -f1)
echo "$SHA256" > "${ZIP}.sha256"

# Also attempt DMG for user-friendly distribution
DMG_CREATED=0
DMG_LOG="$(mktemp)"
if hdiutil create -volname "DashScribe" -srcfolder "$BUNDLE" -ov -format UDZO "$DMG" >"$DMG_LOG" 2>&1; then
    DMG_CREATED=1
    tail -2 "$DMG_LOG"
else
    echo "  DMG creation skipped (sandbox restriction or hdiutil error)"
fi
rm -f "$DMG_LOG"

echo "=== Build complete ==="
SIZE=$(du -sh "$BUNDLE" | cut -f1)
echo "  Version: ${VERSION}"
echo "  Bundle:  $BUNDLE ($SIZE)"
ZIP_SIZE=$(du -sh "$ZIP" | cut -f1)
echo "  ZIP:     $ZIP ($ZIP_SIZE)"
echo "  SHA256:  $SHA256"
if [ "$DMG_CREATED" -eq 1 ]; then
    DMG_SIZE=$(du -sh "$DMG" | cut -f1)
    echo "  DMG:     $DMG ($DMG_SIZE)"
fi
echo ""
echo "To install: cp -R $BUNDLE /Applications/"
echo "To test:    $BUNDLE/Contents/MacOS/DashScribe"
echo ""
echo "To release: ./release.sh"
