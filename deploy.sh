#!/bin/bash
# Deploy OptionPilot to the home server. Rsyncs the working tree (including
# .env) and rebuilds. Never touches /opt/trading_bot.
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-ldanielrod@192.168.100.229}"
REMOTE_PATH="/opt/optionpilot"

echo "==> rsync to $REMOTE_HOST:$REMOTE_PATH"
rsync -az --delete \
    --exclude '.git' --exclude '__pycache__' --exclude '.venv' \
    --exclude 'reports/' \
    ./ "$REMOTE_HOST:$REMOTE_PATH/"

echo "==> build + restart"
# single commands per ssh call; no heredocs (see trading_bot deploy.sh bug:
# an exec inside a heredoc ate the rest of the script via inherited stdin)
ssh "$REMOTE_HOST" "cd $REMOTE_PATH && docker compose build --quiet" </dev/null
ssh "$REMOTE_HOST" "cd $REMOTE_PATH && docker compose up -d" </dev/null

echo "==> status"
ssh "$REMOTE_HOST" "cd $REMOTE_PATH && docker compose ps" </dev/null
ssh "$REMOTE_HOST" "docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'trading|telegram|postgres'" </dev/null

echo "==> done. Tail logs with:"
echo "    ssh $REMOTE_HOST 'cd $REMOTE_PATH && docker compose logs -f --tail 50 optionpilot_agent'"
