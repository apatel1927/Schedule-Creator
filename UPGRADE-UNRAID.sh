#!/bin/sh
# Upgrade ShiftLedger on Unraid. Keeps old source, existing credentials and appdata.
set -eu
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo 'Docker and Docker Compose are required on the Unraid host.'
  exit 1
fi

if [ ! -f .env ]; then
  if [ "$#" -lt 1 ]; then
    echo 'Pass the path of your PREVIOUS installation containing .env:'
    echo 'sh UPGRADE-UNRAID.sh /mnt/user/appdata/shiftledger-installer/Schedule-Creator-main'
    exit 1
  fi
  previous=$1
  if [ -f "$previous/.env" ]; then
    source_env=$previous/.env
  elif [ -f "$previous/shiftledger/.env" ]; then
    source_env=$previous/shiftledger/.env
  else
    echo "No previous .env found under: $previous"
    echo 'Stopping. Your existing login credentials must be preserved.'
    exit 1
  fi
  umask 077
  cp -p "$source_env" .env
  echo 'Copied previous login configuration to this new upgrade folder.'
else
  echo 'Existing .env in this folder will be preserved.'
fi

if [ ! -f /mnt/user/appdata/shiftledger/data/shiftledger.sqlite3 ]; then
  echo 'No existing database found. For a new installation, run sh INSTALL-UNRAID.sh.'
  exit 1
fi

if ! docker inspect shiftledger >/dev/null 2>&1; then
  echo 'Existing shiftledger container not found; cannot take a verified live backup.'
  echo 'Do not continue until you have a verified backup of the old SQLite database.'
  exit 1
fi

echo 'Backing up the live database before deploying new code...'
if ! docker exec shiftledger python /app/backup.py; then
  echo 'Database backup failed; upgrade cancelled. Existing container was not changed.'
  exit 1
fi

echo 'Rebuilding and updating ShiftLedger. Data stays under /mnt/user/appdata/shiftledger/data.'
docker compose up --detach --build

echo 'Waiting for application health...'
count=0
while [ "$count" -lt 20 ]; do
  status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}' shiftledger 2>/dev/null || true)
  if [ "$status" = healthy ]; then
    port=$(sed -n 's/^HOST_PORT=//p' .env | head -n 1)
    [ -n "$port" ] || port=8787
    echo "Upgrade complete. Open http://YOUR-UNRAID-IP:$port"
    echo 'Existing stores, shifts, hours, and user logins are preserved.'
    exit 0
  fi
  count=$((count + 1))
  sleep 2
done
echo 'Container health is not confirmed. Inspect: docker compose ps && docker compose logs --tail=100'
echo 'Database and backups were preserved. Do not delete your old source folder.'
exit 1
