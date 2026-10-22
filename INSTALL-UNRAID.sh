#!/bin/sh
set -eu

cd "$(dirname "$0")"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Run this installer from your Unraid terminal."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose is not available. Install Unraid Compose Manager or the Compose plugin first."
  exit 1
fi

if [ ! -f .env ]; then
  if [ -f /mnt/user/appdata/shiftledger/data/shiftledger.sqlite3 ]; then
    echo 'An existing ShiftLedger database was found, but this source folder has no .env.'
    echo 'A newly generated admin password will NOT unlock an existing database.'
    echo 'Instead, run UPGRADE-UNRAID.sh with the previous install-folder path to preserve logins.'
    echo 'Nothing was changed.'
    exit 1
  fi
  umask 077
  if command -v openssl >/dev/null 2>&1; then
    initial_password=$(openssl rand -hex 18)
  else
    initial_password=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
  fi
  if [ "${#initial_password}" -lt 12 ]; then
    echo "Could not generate an administrator password."
    exit 1
  fi
  {
    printf 'ADMIN_USERNAME=admin\n'
    printf 'ADMIN_PASSWORD=%s\n' "$initial_password"
    printf 'HOST_PORT=8787\n'
    printf 'TZ=America/New_York\n'
  } > .env
  echo "FIRST RUN — SAVE THESE CREDENTIALS IN A PASSWORD MANAGER:"
  echo "Username: admin"
  echo "Password: $initial_password"
  echo "The password is also in the private .env file. You can change it from the app."
else
  echo "Existing .env found. Preserving existing login settings and database."
fi

mkdir -p /mnt/user/appdata/shiftledger/data
docker compose up --build --detach
port=$(sed -n 's/^HOST_PORT=//p' .env | head -n 1)
[ -n "$port" ] || port=8787
echo ""
echo "ShiftLedger has been started. Open http://YOUR-UNRAID-IP:$port in a browser."
echo "All employee data is preserved at /mnt/user/appdata/shiftledger/data"
echo "Check status with: docker compose ps"
