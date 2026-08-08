#!/bin/sh
# If CRON_SCHEDULE is set, run as a scheduled daemon.
# If not, run once and exit (original one-shot behaviour).
#
# CRON_SCHEDULE format: standard 5-field cron expression, e.g.
#   "0 8 * * *"   → every day at 08:00
#   "0 8,20 * * *" → every day at 08:00 and 20:00
#
# Logs from cron jobs are forwarded to stdout so `docker logs` works normally.

set -e

if [ -z "$CRON_SCHEDULE" ]; then
    exec python -m app.main
fi

echo "Starting in scheduled mode: CRON_SCHEDULE='$CRON_SCHEDULE'"

# Write the crontab — redirect output to stdout/stderr of PID 1 so docker logs captures it
CRONTAB_FILE=/etc/cron.d/tr-sync
printf '%s root cd /app && python -m app.main >> /proc/1/fd/1 2>> /proc/1/fd/2\n' \
    "$CRON_SCHEDULE" > "$CRONTAB_FILE"
chmod 0644 "$CRONTAB_FILE"

# Export environment variables so cron jobs inherit them
env > /etc/environment

# Start cron daemon in foreground
exec cron -f
