#!/usr/bin/env bash
# Read-only. Proves the first-customer Railway PROJECT token authenticates against the disposable project and an environment named
# `first-customer` that is neither the recovery environment nor any existing QA/production identifier. Prints ids (not secrets).
set -uo pipefail
EXP_PROJECT=482d0eff-ecd3-4457-8834-e0b49e897e42
EXP_ENV=c85bcb0c-9b82-45a6-90f7-67793520d8a6
FORBIDDEN=(43eadd7f-40be-4cdf-acb8-e1a8b33ba3b8 a16eaf0a-b5a5-4e8e-b79e-4d191457638e f948241d-446f-43ed-bff0-5d12decffd50)
[[ -n "${RAILWAY_TOKEN:-}" ]] || { echo "BLOCKED: GEOCORE_DISPOSABLE_FIRSTCUSTOMER_RAILWAY_TOKEN is missing or empty (owner action)"; exit 3; }
gql() { curl -sS --max-time 40 https://backboard.railway.com/graphql/v2 -H "Project-Access-Token: $RAILWAY_TOKEN" -H 'Content-Type: application/json' -d "$1"; }
scope="$(gql '{"query":"query { projectToken { projectId environmentId } }"}')"
pid="$(jq -r '.data.projectToken.projectId // empty' <<<"$scope")"; eid="$(jq -r '.data.projectToken.environmentId // empty' <<<"$scope")"
[[ -n "$pid" && -n "$eid" ]] || { echo "FAIL: not a project token (no scope returned)"; exit 1; }
rc=0
[[ "$pid" == "$EXP_PROJECT" ]] && echo "PASS project id is the disposable project" || { echo "FAIL project id is not the disposable project"; rc=1; }
[[ "$eid" == "$EXP_ENV" ]] && echo "PASS environment id equals the owner-stated first-customer environment" || { echo "FAIL environment id is not the owner-stated first-customer environment"; rc=1; }
for f in "${FORBIDDEN[@]}"; do [[ "$eid" == "$f" || "$pid" == "$f" ]] && { echo "FAIL token scope is a forbidden identifier (recovery/QA)"; rc=1; }; done
export RAILWAY_TOKEN
status="$(railway status --json 2>/dev/null)" || { echo "FAIL railway status failed"; exit 1; }
name="$(jq -r --arg id "$eid" '[.environments.edges[]?.node|select(.id==$id)|.name]|first // empty' <<<"$status")"
[[ "$name" == "first-customer" ]] && echo "PASS token environment is named first-customer (id $eid)" || { echo "FAIL token environment name is '$name', expected first-customer"; rc=1; }
n="$(jq -r --arg id "$eid" '[.environments.edges[]?.node|select(.id==$id)|.serviceInstances.edges[]?]|length' <<<"$status")"
echo "INFO services in the environment: $n (expected 0 before provisioning)"
vols="$(gql '{"query":"query { projectToken { projectId } }"}' >/dev/null; gql "$(jq -nc --arg p "$pid" '{query:"query($p:String!){project(id:$p){volumes{edges{node{id}}} services{edges{node{id}}}}}",variables:{p:$p}}')" | jq -r '"project-wide services=\([.data.project.services.edges[]]|length) volumes=\([.data.project.volumes.edges[]]|length)"')"
echo "INFO $vols"
exit $rc
