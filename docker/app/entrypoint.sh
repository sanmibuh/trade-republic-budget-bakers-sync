#!/bin/sh
# Entrypoint for the TR→BudgetBakers sync container.
#
# Reads instance names and per-instance schedules from the YAML config file
# (mounted at /app/config/instances.yml) via `python -m app list-schedules`
# (emits "name<TAB>schedule" per line).
# Reads the optional backup schedule via `python -m app get-backup-schedule`.
# After registering cron jobs, starts the cron daemon in the background and
# then starts the Telegram bot as a background process. The shell remains as
# PID 1, traps SIGTERM/INT, and waits for the bot to exit — ensuring both
# processes are stopped cleanly when the container is shut down.
# All instances log to the shared {DATA_DIR}/logs/sync.log file.
#
# CMD override: if set, run a one-shot command and exit.
#   CMD="sync --instance user1"
#   CMD="backup auto"
#   CMD="backup monthly 2026-07"

set -e

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# ------------------------------------------------------------------
# One-shot via CMD (override)
# ------------------------------------------------------------------
if [ -n "$CMD" ]; then
    log "Running: python -m app $CMD"
    # shellcheck disable=SC2086
    exec python -m app $CMD
fi

# ------------------------------------------------------------------
# Multi-instance mode
# ------------------------------------------------------------------
log "Loading instances from /app/config/instances.yml"

# Read per-instance schedules from the YAML: each line is "name<TAB>schedule".
SCHEDULES=$(python -m app list-schedules) || {
    log "ERROR: failed to load schedules from /app/config/instances.yml"
    exit 1
}

if [ -z "$SCHEDULES" ]; then
    log "ERROR: no sync schedules found in /app/config/instances.yml (set sync.schedule or a per-instance schedule)"
    exit 1
fi

# Read optional backup schedule from the YAML.
BACKUP_SCHEDULE_YAML=$(python -m app get-backup-schedule) || {
    log "ERROR: failed to read backup_schedule from /app/config/instances.yml"
    exit 1
}

ENV_FILE=/etc/cron_env
printenv | while IFS='=' read -r key value; do
    # Skip keys that are not valid shell identifiers to prevent sourcing errors.
    case "$key" in
        *[!A-Za-z0-9_]*|[0-9]*|"") continue ;;
    esac
    printf 'export %s=%s\n' "$key" "$(printf '%s' "$value" | sed "s/'/'\\\\''/g;s/.*/'&'/")"
done > "$ENV_FILE"
chmod 600 "$ENV_FILE"

CRONTAB_FILE=/etc/cron.d/tr-sync
printf 'SHELL=/bin/sh\n' > "$CRONTAB_FILE"

printf '%s\n' "$SCHEDULES" | while IFS=$(printf '\t') read -r INSTANCE_NAME INSTANCE_SCHEDULE; do
    log "Registering sync cron for instance '$INSTANCE_NAME': $INSTANCE_SCHEDULE"
    printf "%s root . %s; cd /app && python -m app sync --instance '%s' > /proc/1/fd/1 2>/proc/1/fd/2\n" \
        "$INSTANCE_SCHEDULE" "$ENV_FILE" "$INSTANCE_NAME" >> "$CRONTAB_FILE"
done

if [ -n "$BACKUP_SCHEDULE_YAML" ]; then
    log "Registering backup cron: $BACKUP_SCHEDULE_YAML"
    printf '%s root . %s; cd /app && python -m app backup auto > /proc/1/fd/1 2>/proc/1/fd/2\n' \
        "$BACKUP_SCHEDULE_YAML" "$ENV_FILE" >> "$CRONTAB_FILE"
fi

printf '\n' >> "$CRONTAB_FILE"
chmod 0644 "$CRONTAB_FILE"

log "Crontab registered:"
cat "$CRONTAB_FILE"

log "Starting cron daemon in background"
cron -f &
CRON_PID=$!
# Verify cron started successfully (no race — kill -0 checks the PID directly).
if ! kill -0 "$CRON_PID" 2>/dev/null; then
    log "ERROR: cron failed to start. Aborting."
    exit 1
fi

log "Starting Telegram bot"
python -m app bot &
BOT_PID=$!
# Keep the shell as PID 1 so it can forward signals to both children and
# reap them cleanly when the container is stopped.
trap 'log "Received signal — stopping cron and bot"; kill "$CRON_PID" "$BOT_PID" 2>/dev/null; wait "$CRON_PID" "$BOT_PID" 2>/dev/null; exit 0' TERM INT
# Supervise both processes: if either exits unexpectedly the container
# should restart rather than silently run in a degraded state.
while true; do
    if ! kill -0 "$CRON_PID" 2>/dev/null; then
        log "ERROR: cron exited unexpectedly. Stopping bot and aborting."
        kill "$BOT_PID" 2>/dev/null
        wait "$BOT_PID" 2>/dev/null
        exit 1
    fi
    if ! kill -0 "$BOT_PID" 2>/dev/null; then
        wait "$BOT_PID"
        BOT_EXIT=$?
        log "Bot exited (code $BOT_EXIT). Stopping cron."
        kill "$CRON_PID" 2>/dev/null
        wait "$CRON_PID" 2>/dev/null
        exit "$BOT_EXIT"
    fi
    sleep 5
done
