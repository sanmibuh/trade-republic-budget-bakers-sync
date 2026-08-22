#!/bin/sh
# tr-sync.sh — manage the single TR→BudgetBakers sync container
#
# Usage: ./tr-sync.sh <command> [args...]
#
# Commands:
#   pull                          Pull image from ghcr.io
#   bootstrap <instance>          First-time interactive 2FA login + sync for an instance
#   sync      <instance>          One-shot sync for an instance
#   backup    <mode> [param]      One-shot backup
#   up                            Start container as daemon
#   down                          Stop container
#   upgrade                       Pull + down + up
#   logs                          Follow container logs
#
# Examples:
#   ./tr-sync.sh bootstrap user1
#   ./tr-sync.sh sync user1
#   ./tr-sync.sh backup auto
#   ./tr-sync.sh backup monthly 2026-07
#   ./tr-sync.sh backup yearly 2025
#   ./tr-sync.sh up
#   ./tr-sync.sh upgrade
#   ./tr-sync.sh logs

COMPOSE_FILE="$(dirname "$0")/docker-compose.yml"
COMMAND="$1"
SERVICE="tr-sync"

usage() {
    echo ""
    echo "Usage: $0 <command> [args...]"
    echo ""
    echo "  pull                           Pull image from ghcr.io"
    echo "  bootstrap <instance>           First-time interactive 2FA login + sync"
    echo "  sync      <instance>           One-shot sync (ignores SYNC_SCHEDULE)"
    echo "  backup    <mode> [param]       One-shot backup (auto | monthly | yearly)"
    echo "  up                             Start as daemon"
    echo "  down                           Stop container"
    echo "  upgrade                        Pull + down + up"
    echo "  logs                           Follow container logs"
    echo ""
    echo "Examples:"
    echo "  $0 bootstrap user1"
    echo "  $0 sync user1"
    echo "  $0 backup auto"
    echo "  $0 backup monthly 2026-07"
    echo "  $0 backup yearly 2025"
    echo "  $0 up"
    echo "  $0 upgrade"
    echo "  $0 logs"
    echo ""
}

if [ -z "$COMMAND" ]; then
    usage; exit 1
fi

# Validate instance names to prevent shell metacharacter injection into CMD.
_validate_instance() {
    case "$1" in
        *[!A-Za-z0-9._-]*)
            echo "Error: invalid instance name '$1' (allowed characters: A-Z a-z 0-9 . _ -)"
            exit 1
            ;;
    esac
}

case "$COMMAND" in
    pull)
        docker compose -f "$COMPOSE_FILE" pull "$SERVICE"
        ;;
    bootstrap)
        INSTANCE="$2"
        if [ -z "$INSTANCE" ]; then
            echo "Error: instance name required"
            usage; exit 1
        fi
        _validate_instance "$INSTANCE"
        docker compose -f "$COMPOSE_FILE" run --rm -it \
            -e CMD="sync --instance $INSTANCE" \
            "$SERVICE"
        ;;
    sync)
        INSTANCE="$2"
        if [ -z "$INSTANCE" ]; then
            echo "Error: instance name required"
            usage; exit 1
        fi
        _validate_instance "$INSTANCE"
        docker compose -f "$COMPOSE_FILE" run --rm \
            -e CMD="sync --instance $INSTANCE" \
            "$SERVICE"
        ;;
    backup)
        MODE="$2"
        PARAM="$3"
        if [ -z "$MODE" ]; then
            echo "Error: mode required (auto | monthly | yearly)"
            usage; exit 1
        fi
        case "$MODE" in
            auto|monthly|yearly) ;;
            *)
                echo "Error: invalid backup mode '$MODE' (allowed: auto | monthly | yearly)"
                exit 1
                ;;
        esac
        if [ -n "$PARAM" ]; then
            # PARAM is a date string: YYYY-MM for monthly, YYYY for yearly.
            case "$PARAM" in
                [0-9][0-9][0-9][0-9]-[0-9][0-9]|[0-9][0-9][0-9][0-9]) ;;
                *)
                    echo "Error: invalid param '$PARAM' (expected YYYY-MM or YYYY)"
                    exit 1
                    ;;
            esac
            docker compose -f "$COMPOSE_FILE" run --rm \
                -e CMD="backup $MODE $PARAM" \
                "$SERVICE"
        else
            docker compose -f "$COMPOSE_FILE" run --rm \
                -e CMD="backup $MODE" \
                "$SERVICE"
        fi
        ;;
    up)
        docker compose -f "$COMPOSE_FILE" up -d "$SERVICE"
        ;;
    down)
        docker compose -f "$COMPOSE_FILE" down
        ;;
    upgrade)
        docker compose -f "$COMPOSE_FILE" pull "$SERVICE"
        docker compose -f "$COMPOSE_FILE" down
        docker compose -f "$COMPOSE_FILE" up -d "$SERVICE"
        ;;
    logs)
        docker compose -f "$COMPOSE_FILE" logs -f "$SERVICE"
        ;;
    *)
        echo "Error: unknown command '$COMMAND'"
        usage; exit 1
        ;;
esac
        MODE="$2"
        PARAM="$3"
        if [ -z "$MODE" ]; then
            echo "Error: mode required (auto | monthly | yearly)"
            usage; exit 1
        fi
        case "$MODE" in
            auto|monthly|yearly) ;;
            *)
                echo "Error: invalid backup mode '$MODE' (allowed: auto | monthly | yearly)"
                exit 1
                ;;
        esac
        if [ -n "$PARAM" ]; then
            # PARAM is a date string: YYYY-MM for monthly, YYYY for yearly.
            case "$PARAM" in
                [0-9][0-9][0-9][0-9]-[0-9][0-9]|[0-9][0-9][0-9][0-9]) ;;
                *)
                    echo "Error: invalid param '$PARAM' (expected YYYY-MM or YYYY)"
                    exit 1
                    ;;
            esac
            docker compose -f "$COMPOSE_FILE" run --rm \
                -e CMD="backup $MODE $PARAM" \
                "$SERVICE"
        else
            docker compose -f "$COMPOSE_FILE" run --rm \
                -e CMD="backup $MODE" \
                "$SERVICE"
        fi
        ;;
    up)
        docker compose -f "$COMPOSE_FILE" up -d "$SERVICE"
        ;;
    down)
        docker compose -f "$COMPOSE_FILE" down
        ;;
    upgrade)
        docker compose -f "$COMPOSE_FILE" pull "$SERVICE"
        docker compose -f "$COMPOSE_FILE" down
        docker compose -f "$COMPOSE_FILE" up -d "$SERVICE"
        ;;
    logs)
        docker compose -f "$COMPOSE_FILE" logs -f "$SERVICE"
        ;;
    *)
        echo "Error: unknown command '$COMMAND'"
        usage; exit 1
        ;;
esac
