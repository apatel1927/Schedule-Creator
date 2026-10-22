#!/bin/sh
# Read-only validation after Unraid Docker > Add Container > Apply.
set -eu

image='ghcr.io/apatel1927/schedule-creator:latest'
data_dir='/mnt/user/appdata/shiftledger/data'

docker inspect shiftledger >/dev/null 2>&1 || { echo 'No native shiftledger container yet. Finish Add Container > Apply first.' >&2; exit 1; }
actual_image=$(docker inspect --format '{{.Config.Image}}' shiftledger)
[ "$actual_image" = "$image" ] || { echo "Expected $image, found $actual_image" >&2; exit 1; }
actual_mount=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' shiftledger)
[ "$actual_mount" = "$data_dir" ] || { echo "Wrong appdata mapping ($actual_mount). Stop and correct it before use." >&2; exit 1; }

count=0
while [ "$count" -lt 15 ]; do
  state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' shiftledger)
  [ "$state" = 'healthy' ] && break
  count=$((count+1))
  sleep 2
done
[ "$state" = 'healthy' ] || { echo "Native container is not healthy ($state); inspect Unraid Docker logs." >&2; exit 1; }

echo 'PASS: running the published GHCR image with the original persistent appdata.'
docker exec shiftledger python -c 'import sqlite3; c=sqlite3.connect("/data/shiftledger.sqlite3"); print("Preserved records: " + ", ".join("{}={}".format(t, c.execute("SELECT COUNT(*) FROM {}".format(t)).fetchone()[0]) for t in ("stores","employees","users","shifts","entries")))'
echo 'PASS: Native Docker-managed ShiftLedger is healthy.'
echo 'Future updates: Unraid Docker tab > Check for Updates > Update.'
