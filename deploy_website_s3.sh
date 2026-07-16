#!/bin/bash
# Deploy the marketing website (website/) to S3.
#
# Reads configuration from the repo-root .env: S3_BUCKET (required),
# S3_PREFIX, CLOUDFRONT_DISTRIBUTION_ID, AWS_REGION, AWS_* credentials.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SITE_ROOT="$SCRIPT_DIR/website"
ENV_FILE="$SCRIPT_DIR/../../.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

: "${S3_BUCKET:?S3_BUCKET must be set (see .env)}"
export AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}"

DEST="s3://$S3_BUCKET/${S3_PREFIX:+$S3_PREFIX/}"
DEST="${DEST%/}/"
echo "Deploying website/ to $DEST ..."
aws s3 sync "$SITE_ROOT/" "$DEST" --delete \
  --exclude "*.html" \
  --cache-control "public, max-age=86400"
aws s3 cp "$SITE_ROOT/" "$DEST" --recursive \
  --exclude "*" --include "*.html" \
  --cache-control "no-cache"

if [ -n "${CLOUDFRONT_DISTRIBUTION_ID:-}" ]; then
  echo "Invalidating CloudFront distribution $CLOUDFRONT_DISTRIBUTION_ID ..."
  aws cloudfront create-invalidation \
    --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --paths "/*" >/dev/null
fi

echo "Done: https://$S3_BUCKET"
