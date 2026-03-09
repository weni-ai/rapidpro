#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Usage:
#   ./scripts/upload_org_fixtures.sh <fixtures_dir> <s3_bucket> [s3_prefix]
#
# Examples:
#   ./scripts/upload_org_fixtures.sh org_525_fixtures s3://my-bucket
#   ./scripts/upload_org_fixtures.sh org_525_fixtures s3://my-bucket backups/rapidpro
# ---------------------------------------------------------------------------

FIXTURES_DIR="${1:-}"
S3_BUCKET="${2:-}"
S3_PREFIX="${3:-backups/rapidpro}"

if [[ -z "$FIXTURES_DIR" || -z "$S3_BUCKET" ]]; then
    echo "Usage: $0 <fixtures_dir> <s3_bucket> [s3_prefix]"
    echo ""
    echo "  fixtures_dir  Local directory to compress (e.g. org_525_fixtures)"
    echo "  s3_bucket     S3 bucket URL            (e.g. s3://my-bucket)"
    echo "  s3_prefix     Key prefix inside bucket  (default: backups/rapidpro)"
    exit 1
fi

if [[ ! -d "$FIXTURES_DIR" ]]; then
    echo "Error: directory '$FIXTURES_DIR' not found."
    exit 1
fi

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
ARCHIVE_NAME="${FIXTURES_DIR}_${TIMESTAMP}.tar.gz"
S3_KEY="${S3_PREFIX}/${ARCHIVE_NAME}"
S3_URI="${S3_BUCKET}/${S3_KEY}"

echo "=========================================="
echo "  Fixtures dir : $FIXTURES_DIR"
echo "  Archive      : $ARCHIVE_NAME"
echo "  Destination  : $S3_URI"
echo "=========================================="
echo ""

# ── Step 1: compress ────────────────────────────────────────────────────────
echo "[1/2] Compressing '$FIXTURES_DIR' → '$ARCHIVE_NAME' ..."
tar -czf "$ARCHIVE_NAME" "$FIXTURES_DIR"

SIZE=$(du -sh "$ARCHIVE_NAME" | cut -f1)
echo "      Done. Archive size: $SIZE"
echo ""

# ── Step 2: upload ──────────────────────────────────────────────────────────
echo "[2/2] Uploading to $S3_URI ..."
aws s3 cp "$ARCHIVE_NAME" "$S3_URI" \
    --no-progress \
    --expected-size "$(stat -c%s "$ARCHIVE_NAME")"

echo ""
echo "Upload complete."
echo ""

# ── Cleanup: remove local archive (optional — comment out to keep it) ───────
rm -f "$ARCHIVE_NAME"
echo "Local archive removed: $ARCHIVE_NAME"
echo ""
echo "To download and restore:"
echo "  aws s3 cp $S3_URI ."
echo "  tar -xzf $ARCHIVE_NAME"
echo "  while read f; do python manage.py loaddata \"${FIXTURES_DIR}/\$f\"; done < ${FIXTURES_DIR}/manifest.txt"
