#!/bin/bash
# Push latest code to GitHub and redeploy on NAS.
# Requires .deploy-config — copy .deploy-config.example and fill in your values.
set -e

if [ ! -f .deploy-config ]; then
    echo "Error: .deploy-config not found. Copy .deploy-config.example and fill in your values."
    exit 1
fi

source .deploy-config

echo "→ Pushing to GitHub..."
git push

echo "→ Deploying to $NAS_HOST:$NAS_DEPLOY_PATH..."
# Host port 3001 — 3000 is taken by kana-flash, and the Cloudflare tunnel
# routes japanese.miale13.com → localhost:3001. Build first, and only
# stop/replace the running container once the new image builds cleanly.
ssh root@"$NAS_HOST" "cd $NAS_DEPLOY_PATH && git pull && docker build -t japanese-study . && docker stop japanese-study 2>/dev/null || true && docker rm japanese-study 2>/dev/null || true && docker run -d --name japanese-study --restart unless-stopped -p 3001:8000 -v ${NAS_DEPLOY_PATH}/data:/app/data --env-file ${NAS_DEPLOY_PATH}/.env japanese-study"

echo "✓ Deployed. App available at http://$NAS_HOST:3001 (public: https://japanese.miale13.com)"
