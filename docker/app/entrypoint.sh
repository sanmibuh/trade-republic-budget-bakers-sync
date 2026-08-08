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
#   CMD              If set to "backup [mode] [param]", run a one-shot backup and exit.
#                    Example: CMD="backup auto"  or  CMD="backup monthly 2026-07"
#
# Priority: CMD overrides everything. Otherwise SYNC_SCHEDULE controls daemon vs one-shot.

set -e

# ------------------------------------------------------------------
# One-shot backup via CMD
# ------------------------------------------------------------------
if [ -n "$CMD" ]; then
    case "$CMD" in
        backup*)
            # Strip leading "backup " prefix and pass rest as args
            args="${CMD#backup}"
            args="${args# }"
            echo "Running one-shot backup: python -m app.backup $args"
            # shellcheck disable=SC2086
            exec python -m app.backup $args
            ;;
        *)
            echo "Unknown CMD: $CMD" >&2
            exit 1
            ;;
    esac
fi

# ------------------------------------------------------------------
# One-shot sync (no schedule)
# ------------------------------------------------------------------
if [ -z "$SYNC_SCHEDULE" ]; then
    exec python -m app.main
fi

# ------------------------------------------------------------------
# Daemon mode — register cron jobs and start cron
# ------------------------------------------------------------------
echo "Starting in scheduled mode: SYNC_SCHEDULE='$SYNC_SCHEDULE'"

CRONTAB_FILE=/etc/cron.d/tr-sync

# Sync job
printf '%s root cd /app && python -m app.main >> /proc/1/fd/1 2>> /proc/1/fd/2\n' \
    "$SYNC_SCHEDULE" > "$CRONTAB_FILE"

# Backup job (optional)
if [ -n "$BACKUP_SCHEDULE" ]; then
    echo "Registering backup cron: BACKUP_SCHEDULE='$BACKUP_SCHEDULE'"
    printf '%s root cd /app && python -m app.backup auto >> /proc/1/fd/1 2>> /proc/1/fd/2\n' \
        "$BACKUP_SCHEDULE" >> "$CRONTAB_FILE"
fi

chmod 0644 "$CRONTAB_FILE"

# Export environment variables so cron jobs inherit them
env > /etc/environment

# Start cron daemon in foreground
exec cron -f
