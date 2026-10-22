#!/bin/sh
# Recover the original Compose container after a failed native migration.
# Does not delete the new native container or the persistent database.
set -eu
cd "$(dirname "$0")"

state='/mnt/user/appdata/shiftledger/migration/native-migration.state'
[ -f "$state" ] || { echo 'No native migration state found.' >&2; exit 1; }

legacy_name=$(sed -n 's/^LEGACY_NAME=//p' "$state" | head -n 1)
old_restart=$(sed -n 's/^OLD_RESTART=//p' "$state" | head -n 1)
case "$legacy_name" in shiftledger-legacy-????????-??????) ;; *) echo 'Invalid legacy container name.' >&2; exit 1 ;; esac
case "$old_restart" in always|unless-stopped|on-failure|no|'') ;; *) echo 'Invalid saved restart policy.' >&2; exit 1 ;; esac
docker inspect "$legacy_name" >/dev/null 2>&1 || { echo 'The parked legacy container is missing; nothing was changed.' >&2; exit 1; }

if docker inspect shiftledger >/dev/null 2>&1; then
  current_image=$(docker inspect --format '{{.Config.Image}}' shiftledger)
  case "$current_image" in
    ghcr.io/apatel1927/schedule-creator:*) ;;
    *) echo "Unexpected container image $current_image. Stop to investigate; nothing was changed." >&2; exit 1 ;;
  esac
fi

echo 'This will stop and park the native container (if present), then restart the old Compose container.'
printf 'Type YES to roll back: '
read -r answer || exit 1
[ "$answer" = 'YES' ] || exit 1

if docker inspect shiftledger >/dev/null 2>&1; then
  stamp=$(date -u '+%Y%m%d-%H%M%S')
  docker stop shiftledger
  docker update --restart=no shiftledger >/dev/null
  docker rename shiftledger "shiftledger-native-paused-$stamp"
fi

docker rename "$legacy_name" shiftledger
docker update --restart="${old_restart:-no}" shiftledger >/dev/null
docker start shiftledger
echo 'Original ShiftLedger container restored. Your appdata was not deleted.'
echo 'Keep the backup and both parked containers until you have verified all records.'
