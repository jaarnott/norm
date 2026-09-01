#!/usr/bin/env bash
# Runs as root on EVERY boot, and this box stops and starts daily — so the
# expensive half is guarded by a sentinel and only the cheap, must-be-true
# parts re-run each time.
set -uo pipefail
exec > >(tee -a /var/log/devbox-startup.log) 2>&1
echo "=== devbox startup $(date -Is) ==="

SENTINEL=/var/lib/devbox-provisioned
MARK=v1

# ── Always: keep OS Login users usable with docker ──────────────
# OS Login creates a user the first time they log in, so this cannot be done
# once at provision time — it has to run on each boot to pick up new accounts.
if getent group docker >/dev/null 2>&1; then
  for home in /home/*; do
    u=$(basename "$home")
    id "$u" >/dev/null 2>&1 && usermod -aG docker "$u" 2>/dev/null || true
  done
fi

if [ -f "$SENTINEL" ] && [ "$(cat $SENTINEL)" = "$MARK" ]; then
  echo "already provisioned ($MARK) — skipping install"
else
  echo "--- provisioning ($MARK) ---"
  export DEBIAN_FRONTEND=noninteractive

  apt-get update -y
  apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg git tmux jq unzip build-essential \
    python3 python3-venv python3-pip postgresql-client \
    unattended-upgrades apt-listchanges

  # Security updates apply themselves; this box is yours to patch, and this is
  # the part people forget.
  cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

  # ── Docker ────────────────────────────────────────────────────
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker

  # ── Node 24 + pnpm ────────────────────────────────────────────
  curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
  apt-get install -y nodejs
  corepack enable
  corepack prepare pnpm@latest --activate

  # ── uv (system-wide, so every OS Login user gets it) ───────────
  curl -LsSf https://astral.sh/uv/install.sh \
    | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh

  # ── gh ────────────────────────────────────────────────────────
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    -o /etc/apt/keyrings/githubcli.gpg
  chmod a+r /etc/apt/keyrings/githubcli.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -y && apt-get install -y gh

  # ── cloud-sql-proxy ───────────────────────────────────────────
  curl -fsSL -o /usr/local/bin/cloud-sql-proxy \
    "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.1/cloud-sql-proxy.linux.amd64"
  chmod +x /usr/local/bin/cloud-sql-proxy

  # ── Playwright system libraries ───────────────────────────────
  npx --yes playwright@latest install-deps || echo "WARN: playwright deps failed (non-fatal)"

  echo "$MARK" > "$SENTINEL"
  echo "--- provisioning complete ---"
fi

# ── Always: idle shutdown ───────────────────────────────────────
# The whole cost case rests on this. On-demand at ~217 h/month is roughly a
# third of always-on, and that only holds if stopping is automatic rather than
# remembered.
IDLE_MIN=${idle_shutdown_minutes}

if [ "$IDLE_MIN" -gt 0 ]; then
  cat > /usr/local/bin/devbox-idle-check <<'EOF'
#!/usr/bin/env bash
# Busy if ANY of: someone logged in, real CPU load, Claude running, or an
# explicit hold. The Claude and hold checks matter because a detached tmux
# waiting on an API call looks idle by every other measure — and shutting the
# box down mid-agent-run would be worse than any saving.
set -uo pipefail
THRESHOLD_MIN=$1
STATE=/var/lib/devbox-idle-count
INTERVAL_MIN=5

busy() {
  who | grep -q . && return 0
  [ -f /var/run/devbox-keep-awake ] && return 0
  pgrep -x claude >/dev/null 2>&1 && return 0
  pgrep -f "pytest|vitest|next dev|uvicorn" >/dev/null 2>&1 && return 0
  awk '{ exit ($1 > 0.4) ? 0 : 1 }' /proc/loadavg && return 0
  return 1
}

if busy; then
  echo 0 > "$STATE"
  exit 0
fi

count=$(cat "$STATE" 2>/dev/null || echo 0)
count=$((count + INTERVAL_MIN))
echo "$count" > "$STATE"

if [ "$count" -ge "$THRESHOLD_MIN" ]; then
  logger -t devbox "idle $${count}m >= $${THRESHOLD_MIN}m — shutting down"
  /sbin/shutdown -h now "devbox idle"
fi
EOF
  chmod +x /usr/local/bin/devbox-idle-check

  # Convenience: `keepawake on` before a long unattended run.
  cat > /usr/local/bin/keepawake <<'EOF'
#!/usr/bin/env bash
case "$${1:-status}" in
  on)  sudo touch /var/run/devbox-keep-awake && echo "idle shutdown: HELD OFF" ;;
  off) sudo rm -f /var/run/devbox-keep-awake && echo "idle shutdown: armed" ;;
  *)   [ -f /var/run/devbox-keep-awake ] && echo "HELD OFF" || echo "armed" ;;
esac
EOF
  chmod +x /usr/local/bin/keepawake

  cat > /etc/systemd/system/devbox-idle.service <<EOF
[Unit]
Description=Shut the dev box down when nobody is using it
[Service]
Type=oneshot
ExecStart=/usr/local/bin/devbox-idle-check $IDLE_MIN
EOF

  cat > /etc/systemd/system/devbox-idle.timer <<'EOF'
[Unit]
Description=Check every 5 minutes whether the dev box is idle
[Timer]
OnBootSec=15min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF

  systemctl daemon-reload
  systemctl enable --now devbox-idle.timer
  echo 0 > /var/lib/devbox-idle-count
  echo "idle shutdown armed at $IDLE_MIN minutes"
fi

echo "=== devbox startup done $(date -Is) ==="
