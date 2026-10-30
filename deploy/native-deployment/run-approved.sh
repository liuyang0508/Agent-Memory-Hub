#!/usr/bin/env bash
# Uses the immutable artifact from a SUCCESSFUL preflight; never rebuilds the wheel.
set -euo pipefail
release=e685c89283adfb3fc5ba6306e85bd4b17303b161
[[ "${APPROVED_OPS_SHA:-}" =~ ^[0-9a-f]{40}$ ]] && [ "$GITHUB_SHA" = "$APPROVED_OPS_SHA" ] || exit 2
[ "${DEPLOY_ACK:-}" = "approve-aihub0508.com-$release" ] || exit 2
[[ "${ARTIFACT_RUN_ID:-}" =~ ^[0-9]+$ ]] || exit 2
[[ "${APPROVED_BUNDLE_SHA:-}" =~ ^[0-9a-f]{64}$ ]] || exit 2
[[ "${INVITE_PUBLIC_KEY_B64:-}" =~ ^[A-Za-z0-9+/=]+$ ]] && [ "${#INVITE_PUBLIC_KEY_B64}" -lt 8192 ] || exit 2
artifact_dir=$(mktemp -d)
gh api "repos/$GITHUB_REPOSITORY/actions/runs/$ARTIFACT_RUN_ID" > "$artifact_dir/run.json"
python - "$artifact_dir/run.json" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
assert r['conclusion']=='success' and r['status']=='completed'
assert r['event']=='workflow_dispatch' and r['head_branch']=='codex/native-deploy-prep-20261030'
assert r['path']=='.github/workflows/deploy-official-site.yml'
PY
gh run download "$ARTIFACT_RUN_ID" --repo "$GITHUB_REPOSITORY" --name native-release-e685c89 --dir "$artifact_dir/package"
python deploy/native-deployment/verify_bundle.py "$artifact_dir/package" "$APPROVED_BUNDLE_SHA" >/dev/null
remote="${AIHUB_SSH_USER}@${AIHUB_SSH_HOST}"
opts=(-i "$HOME/.ssh/aihub_key" -p "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
stage=$(ssh "${opts[@]}" "$remote" 'mktemp -d /tmp/amh-native-apply.XXXXXXXX')
[[ "$stage" =~ ^/tmp/amh-native-apply\.[A-Za-z0-9]+$ ]]
# On failure retain this exact tree for inspection; never retry an uncertain rollout blindly.
echo "deployment_stage=$stage"
scp -i "$HOME/.ssh/aihub_key" -P "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned "$artifact_dir/package/manifest.json" deploy/native-deployment/verify_bundle.py deploy/native-deployment/apply.sh deploy/native-deployment/rollback.sh deploy/native-deployment/bootstrap.py "$remote:$stage/"
ssh "${opts[@]}" "$remote" "mkdir '$stage/wheelhouse'"
scp -i "$HOME/.ssh/aihub_key" -P "$AIHUB_SSH_PORT" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=aihub-pinned "$artifact_dir/package/wheelhouse/"*.whl "$remote:$stage/wheelhouse/"
ssh "${opts[@]}" "$remote" "bash '$stage/apply.sh' '$DEPLOY_ACK' '$stage' '$APPROVED_BUNDLE_SHA' '$stage/rollback.sh' '$INVITE_PUBLIC_KEY_B64'" | tee "$artifact_dir/apply.log"
curl -fsS --retry 2 --max-time 15 https://aihub0508.com/native/v1/ready
[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 https://aihub0508.com/native/v1/me)" = 401 ]
curl -fsS --max-time 15 -o /dev/null https://aihub0508.com/
ssh "${opts[@]}" "$remote" "test -f '$stage/apply.sh' && rm -rf -- '$stage'"
backup=$(sed -n 's#^rollback_config=\(/var/backups/amh-native/[0-9T]*Z\)/site.before.conf$#\1#p' "$artifact_dir/apply.log")
[[ "$backup" =~ ^/var/backups/amh-native/[0-9]{8}T[0-9]{6}Z$ ]]
mkdir -p deployed-invite
ssh "${opts[@]}" "$remote" "cat '$backup/initial-invite.enc'" > deployed-invite/initial-invite.enc
cp "$artifact_dir/package/manifest.json" deployed-invite/release-manifest.json
echo 'public_https_and_authorization_verified=true'
