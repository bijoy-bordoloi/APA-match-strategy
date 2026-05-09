#!/bin/bash
# deploy.sh — full-stack deployment for APA Match Strategy
#
# Usage:
#   ./deploy.sh              # deploy everything (Lambda + frontend)
#   ./deploy.sh lambda       # Lambda only
#   ./deploy.sh frontend     # frontend only

set -euo pipefail

REGION="us-west-1"
LAMBDA_FUNCTION="APA-match-strategy"
AMPLIFY_APP_ID="d1r5j1w7p4gz6w"
AMPLIFY_BRANCH="main"
DIR="$(cd "$(dirname "$0")" && pwd)"

TARGET="${1:-all}"

# ── Lambda ───────────────────────────────────────────────────────────────────
deploy_lambda() {
  echo "==> Deploying Lambda..."

  # Sync source files into the deployment package directory
  for f in lambda_function.py chat_handler.py data_access.py match_rules.py \
            config_loader.py groqstrategy.py strategies.py prompts.py \
            mistralstrategy.py aistrategy.py session_loader.py; do
    [ -f "$DIR/$f" ] && cp "$DIR/$f" "$DIR/package/$f"
  done

  # Build zip from package/
  cd "$DIR/package"
  zip -qr "$DIR/function.zip" . -x "*__pycache__*" -x "*.pyc"
  cd "$DIR"

  aws lambda update-function-code \
    --function-name "$LAMBDA_FUNCTION" \
    --zip-file "fileb://$DIR/function.zip" \
    --region "$REGION" \
    --output text --query 'LastUpdateStatus'

  aws lambda wait function-updated \
    --function-name "$LAMBDA_FUNCTION" \
    --region "$REGION"

  echo "    Lambda live: https://cyh1au8vb9.execute-api.$REGION.amazonaws.com"
}

# ── Frontend ─────────────────────────────────────────────────────────────────
deploy_frontend() {
  echo "==> Building frontend..."
  cd "$DIR/frontend"
  npm ci --silent
  npm run build

  cd "$DIR/frontend/dist"
  zip -qr "$DIR/frontend-dist.zip" .
  cd "$DIR"

  echo "==> Uploading frontend to Amplify..."
  DEPLOY=$(aws amplify create-deployment \
    --app-id "$AMPLIFY_APP_ID" \
    --branch-name "$AMPLIFY_BRANCH" \
    --region "$REGION" \
    --output json)

  JOB_ID=$(echo "$DEPLOY" | python3 -c "import sys,json; print(json.load(sys.stdin)['jobId'])")
  ZIP_URL=$(echo "$DEPLOY" | python3 -c "import sys,json; print(json.load(sys.stdin)['zipUploadUrl'])")

  curl -s -X PUT "$ZIP_URL" \
    -H "Content-Type: application/zip" \
    --data-binary "@$DIR/frontend-dist.zip"

  aws amplify start-deployment \
    --app-id "$AMPLIFY_APP_ID" \
    --branch-name "$AMPLIFY_BRANCH" \
    --job-id "$JOB_ID" \
    --region "$REGION" \
    --output text --query 'jobSummary.status'

  echo "    Frontend deploying (job $JOB_ID)."
  echo "    App: https://$AMPLIFY_BRANCH.$AMPLIFY_APP_ID.amplifyapp.com"
}

# ── Dispatch ─────────────────────────────────────────────────────────────────
case "$TARGET" in
  lambda)   deploy_lambda ;;
  frontend) deploy_frontend ;;
  all)      deploy_lambda && deploy_frontend ;;
  *)
    echo "Usage: $0 [lambda|frontend|all]"
    exit 1
    ;;
esac

echo ""
echo "==> Done."
