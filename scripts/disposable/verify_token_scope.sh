#!/usr/bin/env bash
# Read-only. Proves that the supplied Railway PROJECT token authenticates against EXACTLY the expected disposable project and
# environment, and against nothing else. Prints booleans and counts only; never the token, never a credential.
# usage: RAILWAY_TOKEN=... verify_token_scope.sh <expected-project-id> <expected-environment-id> <expected-environment-name>
set -uo pipefail
EXP_PROJECT="$1"; EXP_ENV="$2"; EXP_ENV_NAME="$3"
FORBIDDEN_IDS=(
  a16eaf0a-b5a5-4e8e-b79e-4d191457638e   # existing isolated QA project
  f948241d-446f-43ed-bff0-5d12decffd50   # existing isolated QA environment
  40d0cad3-0c01-467a-910e-6c9f818fd2bf   # existing QA API service
  8aa9dce6-49c9-4da2-b4a7-288de10e0904   # existing QA web service
  fe3ac31d-4b80-4db4-aad5-f9f2c7f83bfd   # existing QA PostgreSQL service
)
[[ -n "${RAILWAY_TOKEN:-}" ]] || { echo "BLOCKED: the token secret is missing or empty (owner action: add it to the repository secrets)"; exit 3; }
gql() { curl -sS --max-time 40 https://backboard.railway.com/graphql/v2 -H "Project-Access-Token: $RAILWAY_TOKEN" -H 'Content-Type: application/json' -d "$1"; }
scope="$(gql '{"query":"query { projectToken { projectId environmentId } }"}')"
pid="$(jq -r '.data.projectToken.projectId // empty' <<<"$scope")"
eid="$(jq -r '.data.projectToken.environmentId // empty' <<<"$scope")"
if [[ -z "$pid" || -z "$eid" ]]; then echo "FAIL: the token did not authenticate as a Railway project token (no scope returned)"; exit 1; fi
rc=0
[[ "$pid" == "$EXP_PROJECT" ]] && echo "PASS token project id equals the expected disposable project" || { echo "FAIL token project id is NOT the expected disposable project"; rc=1; }
[[ "$eid" == "$EXP_ENV" ]] && echo "PASS token environment id equals the expected $EXP_ENV_NAME environment" || { echo "FAIL token environment id is NOT the expected $EXP_ENV_NAME environment"; rc=1; }
for f in "${FORBIDDEN_IDS[@]}"; do [[ "$pid" == "$f" || "$eid" == "$f" ]] && { echo "FAIL token scope is an existing QA identifier"; rc=1; }; done
# Read-only inventory through the CLI (project tokens are accepted by `railway status`).
export RAILWAY_TOKEN
if ! status="$(railway status --json 2>/dev/null)"; then echo "FAIL railway status failed"; exit 1; fi
name="$(jq -r '.name // empty' <<<"$status")"
[[ "$name" == geocore-disposable-qa-* ]] && echo "PASS project name has the disposable prefix" || { echo "FAIL project name does not have the disposable prefix"; rc=1; }
envs="$(jq -r '[.environments.edges[]?.node | "\(.name)=\(.id)"] | join(",")' <<<"$status")"
jq -e --arg id "$EXP_ENV" --arg n "$EXP_ENV_NAME" '[.environments.edges[]?.node | select(.id==$id and .name==$n)] | length == 1' <<<"$status" >/dev/null \
  && echo "PASS the expected environment exists with the expected name" || { echo "FAIL the expected environment/name pair was not found"; rc=1; }
svc="$(jq -r '[.environments.edges[]?.node | select(.id==$id) | .serviceInstances.edges[]?] | length' --arg id "$EXP_ENV" <<<"$status" 2>/dev/null || echo unknown)"
echo "INFO services in the environment: ${svc:-unknown} (expected 0 before provisioning)"
exit $rc
