#!/bin/sh
# Entrypoint for the TR→BudgetBakers sync container.
#
# Behaviour is controlled by the MODE environment variable:
#
#   MODE=sync     Run as sync daemon (cron) or one-shot sync.
#                 SYNC_SCHEDULE — cron expression (e.g. "0 8,20 * * *").
#                 If empty / unset → run one-shot sync and exit.
#
#   MODE=backup   Run as backup daemon (cron) or one-shot backup.
#                 BACKUP_SCHEDULE — cron expression (e.g. "0 3 * * *").
#                 If empty / unset → run one-shot backup auto and exit.
#
#   MODE=bot      Start the Telegram bot for remote command execution.
#
# Multi-instance mode (activated when INSTANCES_CONFIG is set):
#   Reads instance names from the YAML config file and registers one cron job
#   per instance (python -m app sync --instance <name>) using SYNC_SCHEDULE.
#   Optionally registers the backup job when BACKUP_SCHEDULE is also set.
#   The MODE env var is ignored in this mode.
#   After registering cron jobs, starts the cron daemon in the background and
#   then starts the Telegram bot as a background process. The shell remains as
#   PID 1, traps SIGTERM/INT, and waits for the bot to exit — ensuring both
#   processes are stopped cleanly when the container is shut down.
#   All instances log to the shared {DATA_DIR}/logs/sync.log file.
#
# CMD override: if set, run a one-shot command and exit regardless of MODE.
#   CMD="sync"
#   CMD="backup auto"
#   CMD="backup monthly 2026-07"

set -e

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# ------------------------------------------------------------------
# One-shot via CMD (override for all modes)
# ------------------------------------------------------------------
if [ -n "$CMD" ]; then
    log "Running: python -m app $CMD"
    # shellcheck disable=SC2086
    exec python -m app $CMD
fi

# ------------------------------------------------------------------
# Multi-instance mode (INSTANCES_CONFIG is set)
# ------------------------------------------------------------------
if [ -n "$INSTANCES_CONFIG" ]; then
    if [ -z "$SYNC_SCHEDULE" ]; then
        log "ERROR: INSTANCES_CONFIG is set but SYNC_SCHEDULE is empty. Cannot register cron jobs."
        exit 1
    fi

    log "Multi-instance mode: loading instances from $INSTANCES_CONFIG"

    INSTANCES=$(python -m app list-instances) || {
        log "ERROR: failed to load instances from $INSTANCES_CONFIG"
        exit 1
    }

    if [ -z "$INSTANCES" ]; then
        log "ERROR: no instances found in $INSTANCES_CONFIG"
        exit 1
    fi

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

    for INSTANCE_NAME in $INSTANCES; do
        log "Registering sync cron for instance '$INSTANCE_NAME': $SYNC_SCHEDULE"
        printf "%s root . %s; cd /app && python -m app sync --instance '%s' > /proc/1/fd/1 2>/proc/1/fd/2\n" \
            "$SYNC_SCHEDULE" "$ENV_FILE" "$INSTANCE_NAME" >> "$CRONTAB_FILE"
    done

    if [ -n "$BACKUP_SCHEDULE" ]; then
        log "Registering backup cron: $BACKUP_SCHEDULE"
        printf '%s root . %s; cd /app && python -m app backup auto > /proc/1/fd/1 2>/proc/1/fd/2\n' \
            "$BACKUP_SCHEDULE" "$ENV_FILE" >> "$CRONTAB_FILE"
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
    trap 'log "Received signal — stopping cron and bot"; kill "$CRON_PID" "$BOT_PID" 2>/dev/null' TERM INT
    wait "$BOT_PID"
    BOT_EXIT=$?
    kill "$CRON_PID" 2>/dev/null
    wait "$CRON_PID" 2>/dev/null
    exit "$BOT_EXIT"
fi

# ------------------------------------------------------------------
# Bot mode
# ------------------------------------------------------------------
if [ "$MODE" = "bot" ]; then
    log "Starting Telegram bot"
    exec python -m app bot
fi

# ------------------------------------------------------------------
# Helpers: write env file + crontab and start cron daemon
# ------------------------------------------------------------------
_start_cron() {
    SCHEDULE="$1"
    COMMAND="$2"
    LABEL="$3"

    log "Starting in scheduled mode: $LABEL='$SCHEDULE'"

    # Export environment variables as a sourceable shell script so cron jobs
    # inherit them reliably (avoids /etc/environment parsing issues with
    # special characters in values).
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
    printf '%s root . %s; cd /app && python -m app %s > /proc/1/fd/1 2>/proc/1/fd/2\n' \
        "$SCHEDULE" "$ENV_FILE" "$COMMAND" >> "$CRONTAB_FILE"
    printf '\n' >> "$CRONTAB_FILE"
    chmod 0644 "$CRONTAB_FILE"

    log "Crontab registered:"
    cat "$CRONTAB_FILE"

    exec cron -f
}

# ------------------------------------------------------------------
# Sync mode
# ------------------------------------------------------------------
if [ "$MODE" = "sync" ]; then
    if [ -z "$SYNC_SCHEDULE" ]; then
        exec python -m app sync
    fi
    _start_cron "$SYNC_SCHEDULE" "sync" "SYNC_SCHEDULE"
fi

# ------------------------------------------------------------------
# Backup mode
# ------------------------------------------------------------------
if [ "$MODE" = "backup" ]; then
    if [ -z "$BACKUP_SCHEDULE" ]; then
        exec python -m app backup auto
    fi
    _start_cron "$BACKUP_SCHEDULE" "backup auto" "BACKUP_SCHEDULE"
fi

log "ERROR: MODE='$MODE' is not set or not recognised. Valid values: sync, backup, bot"
exit 1
