#!/bin/bash
# Build the Workflow Studio frontend and deploy it to S3.
#
# Reads configuration from the repo-root .env (see the "Frontend (app) S3
# deployment" section): FRONTEND_S3_BUCKET (required), FRONTEND_S3_PREFIX,
# FRONTEND_CLOUDFRONT_DISTRIBUTION_ID, FRONTEND_API_BASE_URL, AWS_* creds.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
FRONTEND_ROOT="$SCRIPT_DIR/frontend"
ENV_FILE="$SCRIPT_DIR/../../.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

: "${FRONTEND_S3_BUCKET:?FRONTEND_S3_BUCKET must be set (see .env)}"
export AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}"

# The API base is baked into the bundle at build time. The API token is
# not: users are prompted in the browser on first use.
API_BASE="${FRONTEND_API_BASE_URL:-https://api.genxflowstudio.com/api/v1}"
API_BASE="${API_BASE%/api/v1}"  # frontend appends /api/v1 itself

cd "$FRONTEND_ROOT"
[ -d node_modules ] || npm install --no-audit --no-fund
echo "Building with VITE_API_BASE=$API_BASE ..."
VITE_API_BASE="$API_BASE" npm run build

DEST="s3://$FRONTEND_S3_BUCKET/${FRONTEND_S3_PREFIX:+$FRONTEND_S3_PREFIX/}"
DEST="${DEST%/}/"
echo "Deploying dist/ to $DEST ..."
# Hashed assets can cache forever; index.html must always revalidate.
aws s3 sync dist/ "$DEST" --delete \
  --exclude index.html \
  --cache-control "public, max-age=31536000, immutable"
aws s3 cp dist/index.html "${DEST}index.html" \
  --cache-control "no-cache"

if [ -n "${FRONTEND_CLOUDFRONT_DISTRIBUTION_ID:-}" ]; then
  echo "Invalidating CloudFront distribution $FRONTEND_CLOUDFRONT_DISTRIBUTION_ID ..."
  aws cloudfront create-invalidation \
    --distribution-id "$FRONTEND_CLOUDFRONT_DISTRIBUTION_ID" \
    --paths "/*" >/dev/null
fi

echo "Done: https://$FRONTEND_S3_BUCKET"
