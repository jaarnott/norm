#!/usr/bin/env bash
# Start, reach and stop the dev box.
#
# The box shuts itself down when idle, so starting it is a daily action — that
# is the whole cost model, and it needs to be one word, not a console visit.
set -euo pipefail

PROJECT="${DEVBOX_PROJECT:-norm-devbox-ja}"
ZONE="${DEVBOX_ZONE:-australia-southeast1-a}"
NAME="${DEVBOX_NAME:-norm-dev}"
GCLOUD="${GCLOUD:-$HOME/google-cloud-sdk/bin/gcloud}"
command -v gcloud >/dev/null 2>&1 && GCLOUD=gcloud

state() {
  # timeout: against a project that does not exist yet, gcloud retries for a
  # long time rather than failing — which makes `status` look like a hang.
  timeout 30 "$GCLOUD" compute instances describe "$NAME" --project="$PROJECT" --zone="$ZONE" \
    --format="value(status)" 2>/dev/null || echo "NOT_FOUND"
}

up() {
  local s; s=$(state)
  case "$s" in
    RUNNING)   echo "$NAME is already running." ;;
    NOT_FOUND) echo "$NAME does not exist in $PROJECT/$ZONE — run terraform apply first." >&2; exit 1 ;;
    *)
      echo "Starting $NAME …"
      "$GCLOUD" compute instances start "$NAME" --project="$PROJECT" --zone="$ZONE" --quiet
      # SSH is not up the moment the API says RUNNING.
      for _ in $(seq 1 30); do
        "$GCLOUD" compute ssh "$NAME" --project="$PROJECT" --zone="$ZONE" \
          --tunnel-through-iap --command=true >/dev/null 2>&1 && break
        sleep 5
      done
      echo "$NAME is up."
      ;;
  esac
}

case "${1:-status}" in
  up) up ;;

  ssh)
    up
    shift || true
    exec "$GCLOUD" compute ssh "$NAME" --project="$PROJECT" --zone="$ZONE" \
      --tunnel-through-iap "$@"
    ;;

  down)
    echo "Stopping $NAME …"
    "$GCLOUD" compute instances stop "$NAME" --project="$PROJECT" --zone="$ZONE" --quiet
    echo "Stopped. Only the boot disk bills from here."
    ;;

  code)
    # Writes ~/.ssh/config entries with an IAP ProxyCommand, which is what lets
    # VS Code Remote-SSH connect to a box that has no external IP.
    up
    "$GCLOUD" compute config-ssh --project="$PROJECT" --quiet
    echo
    echo "In VS Code: Remote-SSH → Connect to Host → $NAME.$ZONE.$PROJECT"
    echo "Then open a folder under ~/projects/."
    ;;

  status)
    s=$(state)
    echo "$NAME ($PROJECT/$ZONE): $s"
    [ "$s" = "RUNNING" ] && "$GCLOUD" compute ssh "$NAME" --project="$PROJECT" --zone="$ZONE" \
      --tunnel-through-iap --command="uptime; keepawake" 2>/dev/null || true
    ;;

  *)
    echo "usage: devbox.sh {up|ssh|down|code|status}" >&2
    exit 2
    ;;
esac
