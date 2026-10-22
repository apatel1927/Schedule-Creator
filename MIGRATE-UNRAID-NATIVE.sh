#!/bin/sh
# One-time move from the legacy Compose container to a native Unraid template.
# Never deletes the old container, image, login files or persistent data.
set -eu
cd "$(dirname "$0")"

image='ghcr.io/apatel1927/schedule-creator:latest'
appdata='/mnt/user/appdata/shiftledger'
data_dir="$appdata/data"
template_dir='/boot/config/plugins/dockerMan/templates-user'
template_source='templates/my-shiftledger.xml'
stamp=$(date -u '+%Y%m%d-%H%M%S')
report_dir="$(pwd)/RESULTS/native-migration-$stamp"
legacy_name="shiftledger-legacy-$stamp"

fail() { printf '\nSTOPPED: %s\n' "$1" >&2; exit 1; }

[ -d /boot/config/plugins/dockerMan ] || fail 'This is not an Unraid Docker installation. Nothing was changed.'
command -v docker >/dev/null 2>&1 || fail 'Docker is not available.'
[ -f "$template_source" ] || fail 'Unraid template is missing from this installer.'
[ -f "$data_dir/shiftledger.sqlite3" ] || fail 'Existing ShiftLedger database not found. Use the native template for a new installation.'
docker inspect shiftledger >/dev/null 2>&1 || fail 'Existing shiftledger container not found. Nothing was changed.'

old_image=$(docker inspect --format '{{.Config.Image}}' shiftledger)
case "$old_image" in
  shiftledger|shiftledger:*) ;;
  "$image") fail 'This is already the registry image. Use the Unraid Docker tab for updates.' ;;
  *) fail "Existing shiftledger uses an unexpected image ($old_image); refusing to alter it." ;;
esac

old_mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' shiftledger)
[ "$old_mount" = "$data_dir" ] || fail "Existing /data mount is $old_mount; expected $data_dir. Refusing to risk another database."

running=$(docker inspect --format '{{.State.Running}}' shiftledger)
[ "$running" = 'true' ] || fail 'The old container is not running. Verify an offline backup before converting.'

echo 'Checking that the published registry image is accessible BEFORE touching the old container...'
docker pull "$image" || fail 'Cannot pull GHCR image. Confirm the GitHub publishing job succeeded and the package is public (or Docker is authenticated). Your old ShiftLedger is untouched.'

mkdir -p "$report_dir" "$appdata/migration"
umask 077
docker inspect shiftledger > "$report_dir/old-container-inspect.json"
old_restart=$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' shiftledger)
case "$old_restart" in always|unless-stopped|on-failure|no|'') ;; *) fail 'Unexpected old restart policy.' ;; esac
printf 'LEGACY_NAME=%s\nOLD_RESTART=%s\nDATE_UTC=%s\n' "$legacy_name" "$old_restart" "$stamp" > "$report_dir/migration.state"

echo 'Creating a verified live SQLite backup before conversion...'
docker exec shiftledger python /app/backup.py || fail 'Backup failed; old container is unchanged.'

mkdir -p "$template_dir"
if [ -f "$template_dir/my-shiftledger.xml" ]; then
  cp -p "$template_dir/my-shiftledger.xml" "$report_dir/previous-unraid-template.xml"
fi
cp -p "$template_source" "$template_dir/my-shiftledger.xml"

echo
echo 'Ready to convert. The old container will be STOPPED and RENAMED, not deleted.'
echo 'Your appdata, old image, credentials and backup remain in place.'
echo 'After conversion, use Unraid: Docker > Add Container > template "shiftledger" > Apply.'
printf 'Type YES to continue: '
read -r answer || fail 'No confirmation received; old container is unchanged.'
[ "$answer" = 'YES' ] || fail 'Cancelled; old container is unchanged.'

echo 'Stopping old Compose container to prevent two writers to the SQLite database...'
docker stop shiftledger || fail 'Could not stop old container. No rename was attempted.'
if ! docker update --restart=no shiftledger >/dev/null; then
  docker start shiftledger >/dev/null || true
  fail 'Could not disable old auto-restart; attempted to restart the original container.'
fi
if ! docker rename shiftledger "$legacy_name"; then
  docker update --restart="$old_restart" shiftledger >/dev/null || true
  docker start shiftledger >/dev/null || true
  fail 'Could not rename old container; attempted to restore its prior state.'
fi

cp "$report_dir/migration.state" "$appdata/migration/native-migration.state"

echo
echo 'Old container safely parked as:' "$legacy_name"
echo 'Native template installed at:' "$template_dir/my-shiftledger.xml"
echo
echo 'NEXT — IN THE UNRAID WEB UI:'
echo '  1. Docker > Add Container.'
echo '  2. Choose template "shiftledger" (under User templates).'
echo '  3. Keep appdata mapped to /mnt/user/appdata/shiftledger/data and port 8787.'
echo '  4. Leave Initial owner password empty (your existing login is preserved).'
echo '  5. Click Apply. Open http://YOUR-UNRAID-IP:8787 and verify your stores.'
echo '  6. Disable automatic startup of the OLD ShiftLedger Compose stack, if enabled.'
echo
echo 'Future releases: Docker tab > Check for Updates > Update.'
echo 'If you need to return to the old container, run: sh ROLLBACK-UNRAID-NATIVE.sh'
echo 'Migration report:' "$report_dir"
