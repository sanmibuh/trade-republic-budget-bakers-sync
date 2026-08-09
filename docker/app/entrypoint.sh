#!/bin/sh
# Entrypoint for the TR→BudgetBakers sync container.
#
# Behaviour is controlled by environment variables:
#
#   SYNC_SCHEDULE    Cron expression for the sync job (e.g. "0 8,20 * * *").
#                    If empty / unset → run one-shot sync and exit.
#
#   BACKUP_SCHEDULE  Cron expression for the daily backup job (e.g. "0 3 * * *").
#                    If empty / unset → no backup cron is registered.
#                    Only meaningful when running as a daemon (SYNC_SCHEDULE set).
#
#   CMD              If set, run a one-shot command and exit.
#                    Passed directly to `python -m app`, e.g.:
#                      CMD="backup auto"
#                      CMD="backup monthly 2026-07"
#
# Priority: CMD overrides everything. Otherwise SYNC_SCHEDULE controls daemon vs one-shot.

set -e

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# ------------------------------------------------------------------
# One-shot via CMD
# ------------------------------------------------------------------
if [ -n "$CMD" ]; then
    log "Running: python -m app $CMD"
    # shellcheck disable=SC2086
    exec python -m app $CMD
fi

# ------------------------------------------------------------------
# One-shot sync (no schedule)
# ------------------------------------------------------------------
if [ -z "$SYNC_SCHEDULE" ]; then
    exec python -m app sync
fi

# ------------------------------------------------------------------
# Daemon mode — register cron jobs and start cron
# ------------------------------------------------------------------
log "Starting in scheduled mode: SYNC_SCHEDULE='$SYNC_SCHEDULE'"

# Export environment variables as a sourceable shell script so cron jobs
# inherit them reliably (avoids /etc/environment parsing issues with
# special characters in values).
ENV_FILE=/etc/cron_env
printenv | while IFS='=' read -r key value; do
    printf 'export %s=%s\n' "$key" "$(printf '%s' "$value" | sed "s/'/'\\\\''/g;s/.*/'&'/")"
done > "$ENV_FILE"
chmod 600 "$ENV_FILE"

CRONTAB_FILE=/etc/cron.d/tr-sync

# Write crontab header
printf 'SHELL=/bin/sh\n' > "$CRONTAB_FILE"

# Sync job — source env file first so all container vars are available
printf '%s root . %s; cd /app && python -m app sync > /proc/1/fd/1 2>/proc/1/fd/2\n' \
    "$SYNC_SCHEDULE" "$ENV_FILE" >> "$CRONTAB_FILE"

# Backup job (optional)
if [ -n "$BACKUP_SCHEDULE" ]; then
    log "Registering backup cron: BACKUP_SCHEDULE='$BACKUP_SCHEDULE'"
    printf '%s root . %s; cd /app && python -m app backup auto > /proc/1/fd/1 2>/proc/1/fd/2\n' \
        "$BACKUP_SCHEDULE" "$ENV_FILE" >> "$CRONTAB_FILE"
fi

# cron requires the file to end with a newline
printf '\n' >> "$CRONTAB_FILE"

chmod 0644 "$CRONTAB_FILE"

log "Crontab registered:"
cat "$CRONTAB_FILE"

# Start cron daemon in foreground
exec cron -f
