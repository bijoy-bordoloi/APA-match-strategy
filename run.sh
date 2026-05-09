#!/bin/bash
# run.sh — start the React app locally against the deployed Lambda API

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$DIR/frontend/.env.local"

if [ ! -f "$ENV_FILE" ]; then
  echo "VITE_API_BASE_URL=https://cyh1au8vb9.execute-api.us-west-1.amazonaws.com" > "$ENV_FILE"
  echo "==> Created $ENV_FILE"
fi

cd "$DIR/frontend"
npm install --silent
npm run dev
