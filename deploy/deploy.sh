#!/usr/bin/env bash
# One-shot server install for the organization memory Hub.
#
# Run this ON the organization's own Linux host, as root:
#
#   ./deploy.sh --hub-url https://amh.企业域名.com \
#               --feishu-app-id cli_xxx \
#               [--feishu-app-secret xxx] \
#               [--tenant 组织标识]
#
# With no --feishu-app-secret it prompts, so the secret never reaches shell
# history or the process list. Everything else can be re-run safely: an existing
# release is upgraded in place and the database is never touched.
set -euo pipefail

hub_url=""
app_id=""
app_secret=""
tenant="linkcrux"
source_dir=""
port=8787
install_root=/opt/amh-native
data_dir=/var/lib/amh-native
env_dir=/etc/amh-native
service=amh-native
auto_nginx=false

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --hub-url) hub_url=${2:-}; shift 2 || usage 2 ;;
    --feishu-app-id) app_id=${2:-}; shift 2 || usage 2 ;;
    --feishu-app-secret) app_secret=${2:-}; shift 2 || usage 2 ;;
    --tenant) tenant=${2:-}; shift 2 || usage 2 ;;
    --source) source_dir=${2:-}; shift 2 || usage 2 ;;
    --port) port=${2:-}; shift 2 || usage 2 ;;
    --nginx) auto_nginx=true; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -n "$hub_url" ] || { echo 'required: --hub-url https://your-hub-domain' >&2; exit 2; }
[ -n "$app_id" ] || { echo 'required: --feishu-app-id' >&2; exit 2; }

# The callback registered in the Feishu console is derived from this value, so a
# typo here produces a login that fails only after the member authorizes.
case "$hub_url" in
  https://*')'|https://*/)
    echo 'hub-url must be https://host with no trailing slash' >&2; exit 2 ;;
  https://*) ;;
  *) echo 'hub-url must start with https:// (Feishu requires TLS)' >&2; exit 2 ;;
esac

if [ -z "$app_secret" ]; then
  # Prompt on the controlling terminal: the documented install pipes this
  # script into bash, so stdin is the script text, not the operator's keys.
  if [ -r /dev/tty ]; then
    read -r -s -p 'Feishu App Secret: ' app_secret </dev/tty
    echo
  else
    echo 'no --feishu-app-secret and no terminal to prompt on' >&2
    exit 2
  fi
fi
[ -n "$app_secret" ] || { echo 'empty app secret' >&2; exit 2; }

# Checked after the arguments: a mistyped flag should be reported as such even
# when the caller forgot sudo, not masked by the root requirement.
[ "$(id -u)" -eq 0 ] || { echo 'must run as root (use sudo)' >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || {
  echo 'systemd (systemctl) is required; this host has none (container?)' >&2
  exit 1
}

say() { printf '==> %s\n' "$*"; }

# --- host prerequisites -------------------------------------------------------
PYTHON=""
for candidate in python3.11 python3.12 python3.13 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null; then
    PYTHON=$(command -v "$candidate"); break
  fi
done
if [ -z "$PYTHON" ]; then
  say 'no Python 3.11+; trying to install one'
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq python3.11 python3.11-venv
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y -q python3.11 python3.11-pip
  else
    echo 'install Python 3.11+ (with venv) and re-run' >&2; exit 1
  fi
  PYTHON=$(command -v python3.11 || command -v python3.12)
fi
say "python: $PYTHON ($("$PYTHON" -V 2>&1))"

# --- source ------------------------------------------------------------------
# Accept a checkout the operator already has; otherwise clone a shallow copy so
# the host does not need to know the full history.
work=$(mktemp -d /tmp/amh-deploy.XXXXXXXX)
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT

if [ -n "$source_dir" ]; then
  [ -f "$source_dir/pyproject.toml" ] || { echo "not a checkout: $source_dir" >&2; exit 2; }
  src=$(cd "$source_dir" && pwd)
else
  command -v git >/dev/null 2>&1 || { echo 'git is required' >&2; exit 1; }
  say 'cloning source'
  git clone --depth=1 "${AMH_SOURCE_URL:-https://github.com/liuyang0508/agent-memory-hub.git}" "$work/src"
  src="$work/src"
fi
release=$(git -C "$src" rev-parse HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)
say "release: $release"

# --- account and directories -------------------------------------------------
id "$service" >/dev/null 2>&1 || useradd --system --user-group \
  --home-dir "$data_dir" --shell /usr/sbin/nologin "$service"
install -d -m 755 "$install_root" "$install_root/releases"
install -d -o "$service" -g "$service" -m 700 "$data_dir"
install -d -m 750 -o root -g "$service" "$env_dir"

version_dir="$install_root/releases/$release"
[ -d "$version_dir" ] || install -d -m 755 "$version_dir"
say "code -> $version_dir"
if [ "$(cd "$src" && pwd)" != "$(cd "$version_dir" && pwd)" ]; then
  tar -C "$src" --exclude=.git --exclude=.native-venv --exclude=node_modules -cf - . |
    tar -C "$version_dir" -xf -
fi

say 'building virtualenv'
[ -d "$version_dir/venv" ] || "$PYTHON" -m venv "$version_dir/venv"
"$version_dir/venv/bin/python" -m pip install --quiet --upgrade pip
"$version_dir/venv/bin/python" -m pip install --quiet --no-cache-dir "$version_dir[web]"

# --- secrets -----------------------------------------------------------------
umask 077
cat > "$env_dir/feishu.env" <<ENV
FEISHU_APP_ID=$app_id
FEISHU_APP_SECRET=$app_secret
AMH_HUB_PUBLIC_URL=$hub_url
FEISHU_TENANT=$tenant
ENV
chown root:"$service" "$env_dir/feishu.env"
chmod 640 "$env_dir/feishu.env"
say "wrote $env_dir/feishu.env (root:$service 0640)"

# --- service -----------------------------------------------------------------
cat > "/etc/systemd/system/$service.service" <<UNIT
[Unit]
Description=AMH organization memory Hub
After=network.target

[Service]
Type=simple
User=$service
Group=$service
WorkingDirectory=$install_root/current
ExecStart=$install_root/current/venv/bin/amh-native serve --db $data_dir/hub.sqlite --host 127.0.0.1 --port $port
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=-$env_dir/feishu.env
Restart=on-failure
RestartSec=3
UMask=0077
StateDirectory=$(basename "$data_dir")
StateDirectoryMode=0700
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$data_dir

[Install]
WantedBy=multi-user.target
UNIT
chmod 644 "/etc/systemd/system/$service.service"

ln -sfn "$version_dir" "$install_root/current.next"
mv -Tf "$install_root/current.next" "$install_root/current"
systemctl daemon-reload
systemctl enable "$service" >/dev/null 2>&1 || true
systemctl restart "$service"

# --- verify ------------------------------------------------------------------
ready=0
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$port/native/v1/ready" >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 0.5
done
if [ "$ready" != 1 ]; then
  echo 'service did not become ready; last log lines:' >&2
  journalctl -u "$service" -n 30 --no-pager >&2 || true
  exit 1
fi

feishu=$(curl -fsS --max-time 5 "http://127.0.0.1:$port/native/v1/auth/feishu" || echo '{}')
case "$feishu" in
  *'"available":true'*) say 'Feishu login: enabled' ;;
  *) echo "warning: Feishu login not enabled yet ($feishu)" >&2 ;;
esac

printf '%s\n' "$release" > "$data_dir/deployed-revision"
chown "$service":"$service" "$data_dir/deployed-revision"

# --- reverse proxy -----------------------------------------------------------
if [ "$auto_nginx" = true ]; then
  if ! command -v nginx >/dev/null 2>&1; then
    echo '--nginx given but nginx is not installed' >&2
  else
    install -d -m 755 /etc/nginx/snippets
    cat > /etc/nginx/snippets/amh-native.conf <<NGINX
location /native/v1/ {
    client_max_body_size 5m;
    proxy_pass http://127.0.0.1:$port;
    proxy_set_header Host \$host;
    proxy_set_header Authorization \$http_authorization;
    proxy_connect_timeout 2s;
    proxy_read_timeout 15s;
}
NGINX
    host=$(printf '%s' "$hub_url" | sed -E 's#^https://##; s#/.*$##')
    target=$(grep -rlE "server_name[^;]*\b${host//./\\.}\b" /etc/nginx 2>/dev/null | head -1 || true)
    if [ -z "$target" ]; then
      echo "no server block for $host found; add this by hand to your HTTPS server block:" >&2
      echo '    include /etc/nginx/snippets/amh-native.conf;' >&2
    else
      cp "$target" "$target.amh-backup"
      # Insert just before the closing brace of the first server block.
      awk 'BEGIN{done=0} {if(!done && $0 ~ /^}/){print "    include /etc/nginx/snippets/amh-native.conf;"; done=1} print}' \
        "$target.amh-backup" > "$target"
      if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx
        say "nginx: included in $target"
      else
        cp "$target.amh-backup" "$target"
        echo 'nginx config test failed; restored the previous file' >&2
      fi
    fi
  fi
fi

echo
say "done. release=$release db=$data_dir/hub.sqlite"
cat <<DONE

Next, in the Feishu open platform console for this app:
  redirect URL : $hub_url/native/v1/auth/feishu/callback
  desktop home : $hub_url/native/v1/admin
  scope        : none required
Then publish a version and approve it.

Issue the operator token (shown once, never sent over HTTP):
  $version_dir/venv/bin/amh-native admin --db $data_dir/hub.sqlite --label "ops name"

Members then run:
  curl -fsSL https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/install.sh \\
    | sh -s -- --org-memory --hub $hub_url
DONE
