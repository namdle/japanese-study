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
ssh root@"$NAS_HOST" "cd $NAS_DEPLOY_PATH && git pull && docker build -t japanese-study . && docker stop japanese-study 2>/dev/null || true && docker rm japanese-study 2>/dev/null || true && docker run -d --name japanese-study --restart unless-stopped -p 3000:8000 -v ${NAS_DEPLOY_PATH}/data:/app/data --env-file ${NAS_DEPLOY_PATH}/.env japanese-study"

echo "✓ Deployed. App available at http://$NAS_HOST:3000"
