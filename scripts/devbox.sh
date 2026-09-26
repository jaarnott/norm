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
    up   # writing the config is pointless if port 22 is not there to reach
    # `gcloud compute config-ssh` CANNOT do this: it has no IAP option at all
    # and writes an entry pointing at an external IP, which this box does not
    # have. So derive the real invocation from `gcloud compute ssh --dry-run`
    # — gcloud's own computed values — and turn it into a Host block that VS
    # Code Remote-SSH can use.
    DRY=$("$GCLOUD" compute ssh "$NAME" --project="$PROJECT" --zone="$ZONE" \
            --tunnel-through-iap --dry-run 2>/dev/null) || {
      echo "could not compute the ssh invocation — is the instance created?" >&2; exit 1; }

    IDENT=$(sed -n 's/.* -i \([^ ]*\).*/\1/p'                       <<<"$DRY")
    ALIAS=$(sed -n 's/.*HostKeyAlias=\([^ ]*\).*/\1/p'              <<<"$DRY")
    KNOWN=$(sed -n 's/.*UserKnownHostsFile=\([^ ]*\).*/\1/p'        <<<"$DRY")
    PROXY=$(sed -n 's/.*-o "ProxyCommand \(.*\)" -o ProxyUseFdpass.*/\1/p' <<<"$DRY")
    SSHUSER=$(awk '{print $NF}' <<<"$DRY"); SSHUSER=${SSHUSER%@*}

    [ -n "$ALIAS" ] && [ -n "$PROXY" ] || { echo "could not parse ssh dry-run" >&2; exit 1; }

    CONF="$HOME/.ssh/config"; mkdir -p "$HOME/.ssh"; touch "$CONF"
    BEGIN="# >>> devbox $NAME >>>"; END="# <<< devbox $NAME <<<"
    # Rewrite in place so re-running never stacks duplicate blocks.
    if grep -qF "$BEGIN" "$CONF"; then
      sed -i "/$(sed 's/[][\.*^$\/]/\\&/g' <<<"$BEGIN")/,/$(sed 's/[][\.*^$\/]/\\&/g' <<<"$END")/d" "$CONF"
    fi
    {
      echo "$BEGIN"
      echo "Host $NAME"
      echo "  HostName $ALIAS"
      echo "  User $SSHUSER"
      [ -n "$IDENT" ] && echo "  IdentityFile $IDENT"
      echo "  IdentitiesOnly yes"
      echo "  CheckHostIP no"
      echo "  HashKnownHosts no"
      echo "  HostKeyAlias $ALIAS"
      [ -n "$KNOWN" ] && echo "  UserKnownHostsFile $KNOWN"
      echo "  ProxyCommand $PROXY"
      echo "$END"
    } >> "$CONF"
    chmod 600 "$CONF"

    echo "Wrote Host \"$NAME\" to $CONF"
    echo
    echo "VS Code: Remote-SSH: Connect to Host…  ->  $NAME"
    echo "Then File > Open Folder > /home/$SSHUSER/projects/norm"
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
