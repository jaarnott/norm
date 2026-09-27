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
  # pnpm PINNED, not @latest. `corepack prepare pnpm@latest` installed 11.x
  # here against the Codespace's 10.x, and pnpm 11 refuses to run dependency
  # build scripts (esbuild, sharp) without an approval list this repo does not
  # carry — so `pnpm install` failed on a box that was supposed to match.
  # corepack's --activate is also per-user, so setting it as root does nothing
  # for the human who logs in; install it globally instead.
  corepack disable pnpm 2>/dev/null || true
  npm install -g pnpm@10.32.1

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

  # ── terraform ─────────────────────────────────────────────────
  # Pinned to match what the Codespace ran; infra/terraform/ (including this
  # module) cannot be planned without it.
  curl -fsSL -o /tmp/tf.zip \
    "https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip"
  unzip -o -q /tmp/tf.zip -d /usr/local/bin && rm -f /tmp/tf.zip
  chmod +x /usr/local/bin/terraform

  # ── cloud-sql-proxy ───────────────────────────────────────────
  curl -fsSL -o /usr/local/bin/cloud-sql-proxy \
    "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.1/cloud-sql-proxy.linux.amd64"
  chmod +x /usr/local/bin/cloud-sql-proxy

  # ── Playwright system libraries ───────────────────────────────
  npx --yes playwright@latest install-deps || echo "WARN: playwright deps failed (non-fatal)"

  echo "$MARK" > "$SENTINEL"
  echo "--- provisioning complete ---"
fi

# ── Always: Cloud SQL proxy for the shared config DB ────────────
# Not just a convenience: tests/conftest.py imports app.db.engine, which
# REFUSES to load when CONFIG_DATABASE_URL is unreachable — so without this
# `pytest` cannot even collect unless the full dev stack happens to be running.
# No --credentials-file; the attached service account supplies credentials.
cat > /etc/systemd/system/cloudsql-config.service <<'UNIT'
[Unit]
Description=Cloud SQL proxy for the shared config DB (port 5433)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/cloud-sql-proxy --address 127.0.0.1 --port 5433 \
  norm-production-491101:australia-southeast1:norm-config
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now cloudsql-config.service 2>/dev/null || true
echo "config-DB proxy service armed"

# ── Always: keep user processes alive with nobody logged in ─────
# WITHOUT THIS, EVERYTHING BELOW IS POINTLESS. systemd stops the per-user
# manager (user@<uid>.service) when the last login session ends, taking its
# whole app.slice with it — every tmux session, the dev servers, and the
# resumed Claude threads. Observed 27 Sep 2026: VS Code disconnected, and 14
# minutes later "Stopped user@515669174.service" killed the lot. It looks fine
# while you are connected, which is exactly when you would test it.
loginctl enable-linger jaarnott_gmail_com 2>/dev/null \
  && echo "linger enabled for jaarnott_gmail_com" \
  || echo "WARN: could not enable linger — tmux sessions will die on logout"

# ── Always: resume Claude threads with Remote Control ───────────
# So the box can be woken from a phone and the conversations are already live
# at claude.ai/code. Works WITH the CPU-delta idle check below: an idle session
# waiting for input does not hold the box awake, a working one does.
cat > /usr/local/bin/devbox-start-threads <<'EOF'
#!/usr/bin/env bash
# Start a Claude session per thread, each with Remote Control, in its own tmux
# session — so after waking the box from a phone the threads are live at
# claude.ai/code and can be driven from there.
#
# The binary lives inside the VS Code extension, whose directory carries the
# extension VERSION — glob it, never hardcode, or the next update breaks this.
set -uo pipefail
PROJECT=/home/jaarnott_gmail_com/projects/norm
LIST=/etc/devbox-threads.conf

CLI=$(ls -1d /home/jaarnott_gmail_com/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | sort -V | tail -1)
if [ -z "$CLI" ] || [ ! -x "$CLI" ]; then
  echo "claude binary not found under ~/.vscode-server/extensions — nothing started" >&2
  exit 0
fi
echo "using $CLI"
[ -f "$LIST" ] || { echo "no $LIST — nothing to start"; exit 0; }

# `--remote-control` alongside `--resume` tries to RE-attach to that thread's
# previous Remote Control session and fails with "Couldn't reconnect to your
# Remote Control session". Issuing /remote-control afterwards opens a fresh one,
# which works. So: start, then drive the slash command.
ensure_rc() {
  local name=$1
  tmux capture-pane -t "$name" -p 2>/dev/null | tail -20 | grep -q "remote-control is active" && return 0
  tmux send-keys -t "$name" "/remote-control" 2>/dev/null; sleep 3
  tmux send-keys -t "$name" Enter 2>/dev/null; sleep 4
  tmux send-keys -t "$name" Enter 2>/dev/null; sleep 20   # confirm "Enable Remote Control"
  tmux capture-pane -t "$name" -p 2>/dev/null | tail -20 | grep -q "remote-control is active"
}

while read -r id label; do
  case "$id" in ''|\#*) continue ;; esac
  label=$${label:-$id}
  name="norm-dev-$label"
  if tmux has-session -t "$name" 2>/dev/null; then echo "  $name already running"; continue; fi
  echo "  starting $name (resume $${id:0:8})"
  tmux new -d -s "$name" "cd $PROJECT && exec '$CLI' --resume '$id' --remote-control '$name'"
  sleep 25
  if ensure_rc "$name"; then echo "    remote control: active"; else echo "    remote control: NOT active"; fi
done < "$LIST"

echo "sessions:"; tmux ls 2>/dev/null | sed 's/^/  /' || echo "  (none)"
EOF
chmod +x /usr/local/bin/devbox-start-threads

# Which threads to resume; one "<session-id>  <label>" per line, # to skip.
# Left empty on a fresh box — populate it once there are conversations worth
# resuming, otherwise the service is a no-op.
[ -f /etc/devbox-threads.conf ] || cat > /etc/devbox-threads.conf <<'EOF'
# <session-id>  <label>     (ids: ~/.claude/projects/<project>/*.jsonl)
EOF

cat > /etc/systemd/system/devbox-threads.service <<'UNIT'
[Unit]
Description=Resume Claude threads with Remote Control on boot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=jaarnott_gmail_com
Environment=HOME=/home/jaarnott_gmail_com
ExecStart=/usr/local/bin/devbox-start-threads
SuccessExitStatus=0 1
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable devbox-threads.service 2>/dev/null || true
echo "thread-resume service armed"

# ── Always: idle shutdown ───────────────────────────────────────
# The whole cost case rests on this. On-demand at ~217 h/month is roughly a
# third of always-on, and that only holds if stopping is automatic rather than
# remembered.
IDLE_MIN=${idle_shutdown_minutes}

if [ "$IDLE_MIN" -gt 0 ]; then
  cat > /usr/local/bin/devbox-idle-check <<'EOF'
#!/usr/bin/env bash
# Busy if ANY of: someone logged in, real CPU load, a claude session that has
# actually DONE work since the last check, a test/dev server running, or an
# explicit hold.
#
# Why claude is measured by CPU delta rather than mere existence: sessions are
# now started on boot (devbox-threads.service) so they are reachable from a
# phone via Remote Control. A bare `pgrep -x claude` would then always match,
# the box would never sleep, and the cost model (~$41/mo at 217h vs ~$139
# always-on) would silently become always-on. An IDLE session waiting for input
# burns ~0 CPU; one actually working burns plenty. So: work keeps the box
# awake, waiting does not.
set -uo pipefail
THRESHOLD_MIN=$1
STATE=/var/lib/devbox-idle-count
CPUSTATE=/var/lib/devbox-claude-cpu
INTERVAL_MIN=5
# Ticks of CPU across all claude processes that count as "it did something".
# Two ticks (~20ms) is far above idle drift and far below any real turn.
CPU_DELTA_TICKS=2

claude_cpu_total() {
  local total=0 pid ut st
  for pid in $(pgrep -x claude 2>/dev/null); do
    read -r _ _ _ _ _ _ _ _ _ _ _ _ _ ut st _ < "/proc/$pid/stat" 2>/dev/null || continue
    total=$((total + ut + st))
  done
  echo "$total"
}

claude_working() {
  local now prev
  now=$(claude_cpu_total)
  prev=$(cat "$CPUSTATE" 2>/dev/null || echo "")
  echo "$now" > "$CPUSTATE"
  [ -z "$prev" ] && return 1                 # first run: no baseline, assume idle
  [ "$now" -lt "$prev" ] && return 1         # counters reset (processes restarted)
  [ $((now - prev)) -ge "$CPU_DELTA_TICKS" ]
}

busy() {
  who | grep -q . && return 0
  [ -f /var/run/devbox-keep-awake ] && return 0
  pgrep -f "pytest|vitest|next dev|uvicorn" >/dev/null 2>&1 && return 0
  awk '{ exit ($1 > 0.4) ? 0 : 1 }' /proc/loadavg && return 0
  claude_working && return 0
  return 1
}

# claude_working MUST run every pass so the CPU baseline stays current, even
# when an earlier check already returned busy — otherwise the first quiet pass
# after a busy spell compares against a stale baseline and looks like work.
claude_working; CLAUDE_BUSY=$?

if who | grep -q . || [ -f /var/run/devbox-keep-awake ] \
   || pgrep -f "pytest|vitest|next dev|uvicorn" >/dev/null 2>&1 \
   || awk '{ exit ($1 > 0.4) ? 0 : 1 }' /proc/loadavg \
   || [ "$CLAUDE_BUSY" -eq 0 ]; then
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
