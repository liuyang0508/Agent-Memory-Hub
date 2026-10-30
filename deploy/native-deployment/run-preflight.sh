#!/usr/bin/env bash
set -euo pipefail
remote="${AIHUB_SSH_USER}@${AIHUB_SSH_HOST}"
opts=(-i "$HOME/.ssh/aihub_key" -p "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
stage=$(ssh "${opts[@]}" "$remote" 'mktemp -d /tmp/amh-native-preflight.XXXXXXXX')
[[ "$stage" =~ ^/tmp/amh-native-preflight\.[A-Za-z0-9]+$ ]]
cleanup() {
  status=$?
  trap - EXIT
  cleanup_status=0
  ssh "${opts[@]}" "$remote" "python3 - '$stage'" < deploy/native-deployment/cleanup_stage.py || cleanup_status=$?
  if [ "$cleanup_status" -ne 0 ]; then
    echo "cleanup failed; retained stage: $stage" >&2
    [ "$status" -ne 0 ] || status=$cleanup_status
  fi
  exit "$status"
}
trap cleanup EXIT
ssh "${opts[@]}" "$remote" "umask 077; touch '$stage/.owned'"
python -m pip wheel --no-deps ./code -w dist >/tmp/amh-native-wheel-build.log 2>&1
wheel=$(find dist -maxdepth 1 -name 'agent_memory_hub-*.whl' -type f)
[ "$(printf '%s\n' "$wheel" | wc -l)" -eq 1 ]
python -m pip download --only-binary=:all: --python-version 311 --implementation cp --abi cp311 --abi abi3 --abi none --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --dest dist/wheelhouse "$wheel[web]" >/tmp/amh-native-dependency-build.log 2>&1
python - "$wheel" <<'MANIFEST'
from pathlib import Path
import hashlib,json,subprocess,sys
sha=subprocess.check_output(['git','-C','code','rev-parse','HEAD'],text=True).strip()
assert sha=='e685c89283adfb3fc5ba6306e85bd4b17303b161'
p=Path(sys.argv[1]);files={q.name:hashlib.sha256(q.read_bytes()).hexdigest() for q in Path('dist/wheelhouse').glob('*.whl')};digest=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest();Path('dist/manifest.json').write_text(json.dumps({'release':sha,'wheel':p.name,'files':files,'bundle_sha256':digest},indent=2)+'\n')
MANIFEST
ssh "${opts[@]}" "$remote" "mkdir '$stage/wheelhouse'"
scp -i "$HOME/.ssh/aihub_key" -P "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned dist/wheelhouse/*.whl "$remote:$stage/wheelhouse/"
scp -i "$HOME/.ssh/aihub_key" -P "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned dist/manifest.json deploy/native-deployment/verify_bundle.py "$remote:$stage/"
bundle_sha=$(python -c 'import json; print(json.load(open("dist/manifest.json"))["bundle_sha256"])')
ssh "${opts[@]}" "$remote" "setsid --wait bash -s -- '$stage' '$stage/wheelhouse/$(basename "$wheel")' '$bundle_sha'" < deploy/native-deployment/preflight.sh
