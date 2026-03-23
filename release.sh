#!/usr/bin/env bash
# Automated release: tag, build, and publish to GitHub Releases.
# Usage: ./release.sh [--dry-run]
set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
    echo "[DRY RUN] No changes will be pushed."
    echo ""
fi

# Read version
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' version.py)
TAG="v${VERSION}"
echo "=== Releasing DashScribe ${TAG} ==="

# Verify prerequisites
if ! command -v gh &>/dev/null; then
    echo "Error: GitHub CLI (gh) is required. Install with: brew install gh"
    exit 1
fi

# Verify clean working tree
if [ -n "$(git status --porcelain)" ]; then
    echo "Error: Working tree is not clean. Commit or stash changes first."
    git status --short
    exit 1
fi

# Check tag doesn't already exist
if git tag -l "$TAG" | grep -q "$TAG"; then
    echo "Error: Tag $TAG already exists."
    echo "  If re-releasing, delete it first: git tag -d $TAG && git push origin :refs/tags/$TAG"
    exit 1
fi

# Find or build the versioned ZIP
ZIP="dist/DashScribe-${VERSION}.zip"
if [ ! -f "$ZIP" ]; then
    echo "ZIP not found at $ZIP — building now..."
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[DRY RUN] Would run ./build_app.sh"
        echo "[DRY RUN] Exiting early — build needed but skipped."
        exit 0
    fi
    ./build_app.sh
fi

# Compute/verify SHA256
SHA256=$(shasum -a 256 "$ZIP" | cut -d' ' -f1)
echo "  ZIP:    $ZIP"
echo "  SHA256: $SHA256"

# Save SHA256 file alongside ZIP
echo "$SHA256" > "${ZIP}.sha256"

if [ "$DRY_RUN" -eq 1 ]; then
    echo ""
    echo "[DRY RUN] Would execute:"
    echo "  git tag $TAG"
    echo "  git push origin $TAG"
    echo "  gh release create $TAG $ZIP ${ZIP}.sha256 \\"
    echo "    --title \"DashScribe ${TAG}\" \\"
    echo "    --notes \"SHA256: ${SHA256}\""
    exit 0
fi

# Create and push tag
echo ""
echo "=== Creating tag ${TAG} ==="
git tag "$TAG"
git push origin "$TAG"

# Create GitHub Release with auto-generated notes + SHA256 appended
echo ""
echo "=== Creating GitHub Release ==="
RELEASE_NOTES=$(gh api repos/{owner}/{repo}/releases/generate-notes \
    -f tag_name="$TAG" --jq '.body' 2>/dev/null || echo "")
RELEASE_NOTES="${RELEASE_NOTES}

SHA256: ${SHA256}"

gh release create "$TAG" \
    "$ZIP" \
    "${ZIP}.sha256" \
    --title "DashScribe ${TAG}" \
    --notes "$RELEASE_NOTES"

echo ""
echo "=== Release ${TAG} published ==="
echo "  https://github.com/ranabirbasu12/DashScribe/releases/tag/${TAG}"
