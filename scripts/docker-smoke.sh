#!/usr/bin/env bash
set -euo pipefail

image="${AMH_DOCKER_SMOKE_IMAGE:-agent-memory-hub:stage1-smoke}"
name="amh-stage1-smoke-${RANDOM}-$$"
volume="amh-stage1-smoke-data-${RANDOM}-$$"
tmp_dir="$(mktemp -d)"

cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

wait_for_health() {
  local output="$1"
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${port}/api/health" >"$output"; then
      python -c 'import json,sys; assert json.load(open(sys.argv[1]))["status"] == "ok"' "$output"
      return 0
    fi
    sleep 1
  done
  docker logs "$name" >&2 || true
  return 1
}

docker build -f deploy/Dockerfile -t "$image" .
docker volume create "$volume" >/dev/null
docker run -d \
  --name "$name" \
  --mount source="$volume",target=/data/brain \
  -p 127.0.0.1::8742 \
  "$image" >/dev/null

port="$(docker port "$name" 8742/tcp | awk -F: 'NR==1 {print $NF}')"
test -n "$port"
wait_for_health "$tmp_dir/health.json"

init_json="$(curl -fsS -X POST "http://127.0.0.1:${port}/api/auth/init" \
  -H 'Content-Type: application/json' \
  -d '{"username":"stage1-admin","password":"stage1-password"}')"
token="$(printf '%s' "$init_json" | python -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
curl -fsS "http://127.0.0.1:${port}/api/auth/me" \
  -H "Authorization: Bearer ${token}" >"$tmp_dir/me.json"
python -c 'import json,sys; assert json.load(open(sys.argv[1]))["username"] == "stage1-admin"' "$tmp_dir/me.json"

docker restart "$name" >/dev/null
wait_for_health "$tmp_dir/health-restart.json"

login_json="$(curl -fsS -X POST "http://127.0.0.1:${port}/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"stage1-admin","password":"stage1-password"}')"
printf '%s' "$login_json" | python -c 'import json,sys; assert json.load(sys.stdin)["username"] == "stage1-admin"'

echo "Docker smoke passed on port ${port}"
