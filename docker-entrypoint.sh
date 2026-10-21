#!/bin/sh
# A Liara disk may be mounted owned by root. Fix the owner of /data, then run the server as an unprivileged user.
set -e
mkdir -p /data/tmp 2>/dev/null || true   # the root filesystem of a Liara app is (nearly) read-only: temporary files go on the disk (TMPDIR)
chown -R agent:agent /data 2>/dev/null || true
exec setpriv --reuid=10001 --regid=10001 --init-groups "$@"
