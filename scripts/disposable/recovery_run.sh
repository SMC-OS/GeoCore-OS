#!/usr/bin/env bash
# Replacement-infrastructure recovery in the DISPOSABLE project's `recovery` environment ONLY (spend cap $3; synthetic data only).
# usage: RAILWAY_TOKEN=<disposable recovery token> IMAGE_REF=<ghcr image@sha256 built from EXPECTED_SHA> REG_USER/REG_TOKEN=<short-lived registry pull credential> EXPECTED_SHA=<40 hex> \
#        recovery_run.sh run <recovery-bundle.tgz> | teardown | inventory
# Teardown ALWAYS runs on exit of `run` (even after a failure). Never prints a credential or a variable value.
set -uo pipefail
PROJ=482d0eff-ecd3-4457-8834-e0b49e897e42
ENVID=43eadd7f-40be-4cdf-acb8-e1a8b33ba3b8
PG_NAME=rec-pg; APP_NAME=rec-app
GQL=https://backboard.railway.com/graphql/v2
REPO="${REPO_SLUG:-SMC-OS/GeoCore-OS}"
ORIG_START='sh -c '"'"'python -m app.core.migrate_gate && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log'"'"''
ORIG_PREDEPLOY='python -m app.core.runtime_check && alembic upgrade head && alembic current'
EVID=${EVID_FILE:-recovery-evidence.txt}
: >"$EVID"
ev() { echo "$*" | tee -a "$EVID"; }
gql() { curl -sS --max-time 90 "$GQL" -H "Project-Access-Token: $RAILWAY_TOKEN" -H 'Content-Type: application/json' -d "$(jq -nc --arg q "$1" --argjson v "${2:-{\}}" '{query:$q,variables:$v}')"; }
gerr() { jq -r '(.errors // [])|map(.message)|join("; ")' <<<"$1"; }

scope_guard() {
  local s; s="$(gql 'query{projectToken{projectId environmentId}}')"
  [[ "$(jq -r .data.projectToken.projectId <<<"$s")" == "$PROJ" && "$(jq -r .data.projectToken.environmentId <<<"$s")" == "$ENVID" ]] \
    || { ev "REFUSED: token is not scoped to the disposable project/recovery environment"; exit 2; }
}

inventory() {  # prints counts only
  local o; o="$(gql 'query($p:String!){project(id:$p){services{edges{node{id name}}} volumes{edges{node{id name}}}}}' "$(jq -nc --arg p "$PROJ" '{p:$p}')")"
  ev "inventory services=$(jq '[.data.project.services.edges[]]|length' <<<"$o") volumes=$(jq '[.data.project.volumes.edges[]]|length' <<<"$o") services:[$(jq -r '[.data.project.services.edges[].node.name]|join(",")' <<<"$o")] volumes:[$(jq -r '[.data.project.volumes.edges[].node.name]|join(",")' <<<"$o")]"
  jq -e '([.data.project.services.edges[]]|length)==0 and ([.data.project.volumes.edges[]]|length)==0' <<<"$o" >/dev/null
}

svc_id() { gql 'query($p:String!){project(id:$p){services{edges{node{id name}}}}}' "$(jq -nc --arg p "$PROJ" '{p:$p}')" | jq -r --arg n "$1" '.data.project.services.edges[].node|select(.name==$n)|.id'; }

teardown() {
  ev "TEARDOWN $(date -u +%FT%TZ)"
  local o id vid
  o="$(gql 'query($p:String!){project(id:$p){services{edges{node{id name}}} volumes{edges{node{id name}}}}}' "$(jq -nc --arg p "$PROJ" '{p:$p}')")"
  for vid in $(jq -r '.data.project.volumes.edges[].node.id' <<<"$o"); do
    r="$(gql 'mutation($v:String!){volumeDelete(volumeId:$v)}' "$(jq -nc --arg v "$vid" '{v:$v}')")"
    ev "volume delete (attached, before service delete): $(jq -r '.data.volumeDelete // "error"' <<<"$r") $(gerr "$r")"
  done
  for id in $(jq -r '.data.project.services.edges[].node.id' <<<"$o"); do
    r="$(gql 'mutation($i:String!,$e:String){serviceDelete(id:$i,environmentId:$e)}' "$(jq -nc --arg i "$id" --arg e "$ENVID" '{i:$i,e:$e}')")"
    ev "service delete: $(jq -r '.data.serviceDelete // "error"' <<<"$r") $(gerr "$r")"
  done
  sleep 10
  o="$(gql 'query($p:String!){project(id:$p){volumes{edges{node{id name}}}}}' "$(jq -nc --arg p "$PROJ" '{p:$p}')")"
  for vid in $(jq -r '.data.project.volumes.edges[].node.id' <<<"$o"); do
    r="$(gql 'mutation($v:String!){volumeDelete(volumeId:$v)}' "$(jq -nc --arg v "$vid" '{v:$v}')")"
    ev "volume delete: $(jq -r '.data.volumeDelete // "error"' <<<"$r") $(gerr "$r")"
  done
  sleep 10
  if inventory; then ev "TEARDOWN VERIFIED: no services and no volumes remain in the disposable project"; return 0; fi
  ev "TEARDOWN INCOMPLETE: a project token is not authorised to delete volumes; the remaining volumes (names above) must be deleted by the owner in the Railway dashboard"; return 1
}

latest_dep() {  # latest_dep <service-name> -> "<id> <status> <commit>"
  railway deployment list --service "$1" --environment "$ENVID" --json --limit 5 2>/dev/null | jq -r 'sort_by(.createdAt)|last|"\(.id) \(.status) \(.meta.commitHash // "n/a")"'
}
wait_dep() {  # wait_dep <service> <status-wanted> <timeout> [previous-deployment-id-to-ignore]
  local dl=$(( $(date +%s) + $3 )) id st c
  while (( $(date +%s) < dl )); do read -r id st c < <(latest_dep "$1"); [[ -n "${4:-}" && "$id" == "$4" ]] && { sleep 10; continue; }; [[ "$st" == "$2" ]] && { echo "$id $c"; return 0; }; [[ "$st" == FAILED || "$st" == CRASHED ]] && { echo "$id $st"; return 1; }; sleep 10; done; echo "timeout"; return 1
}

wait_id() {  # wait_id <service> <deployment-id> <timeout>: that exact deployment must reach SUCCESS
  local dl=$(( $(date +%s) + $3 )) st
  while (( $(date +%s) < dl )); do st="$(railway deployment list --service "$1" --environment "$ENVID" --json --limit 10 2>/dev/null | jq -r --arg i "$2" '.[]|select(.id==$i)|.status')"; [[ "$st" == SUCCESS ]] && { echo "$2"; return 0; }; [[ "$st" == FAILED || "$st" == CRASHED ]] && { echo "$2 $st"; return 1; }; sleep 10; done; echo timeout; return 1
}

upsert_vars() {  # upsert_vars <service-id> <json-object>  (values never printed)
  local r; r="$(gql 'mutation($i:VariableCollectionUpsertInput!){variableCollectionUpsert(input:$i)}' "$(jq -nc --arg p "$PROJ" --arg e "$ENVID" --arg s "$1" --argjson v "$2" '{i:{projectId:$p,environmentId:$e,serviceId:$s,variables:$v,skipDeploys:true}}')")"
  [[ "$(jq -r '.data.variableCollectionUpsert // empty' <<<"$r")" == "true" ]] || { ev "variable upsert failed: $(gerr "$r")"; return 1; }
}

run() {
  local bundle="$1"; : "${EXPECTED_SHA:?}" "${IMAGE_REF:?}" "${REG_USER:?}" "${REG_TOKEN:?}"
  scope_guard
  [[ "$(gql 'query($p:String!){project(id:$p){services{edges{node{id}}}}}' "$(jq -nc --arg p "$PROJ" '{p:$p}')" | jq '[.data.project.services.edges[]]|length')" == 0 ]] && ev "precondition: no services exist (orphan volumes from earlier runs are reported below)" || { ev "REFUSED: services exist; teardown first"; return 2; }
  inventory || true
  trap 'teardown' EXIT
  local t0=$(date +%s); ev "PROVISION START $(date -u +%FT%TZ)"
  local pw jwt seedpw; pw="$(openssl rand -hex 24)"; jwt="$(openssl rand -hex 32)"; seedpw="$(openssl rand -hex 16)"
  echo "::add-mask::$pw"; echo "::add-mask::$jwt"; echo "::add-mask::$seedpw"

  # ---- independent PostgreSQL 18 + independent database volume
  local r pgid
  r="$(gql 'mutation($i:ServiceCreateInput!){serviceCreate(input:$i){id name}}' "$(jq -nc --arg p "$PROJ" --arg e "$ENVID" --arg n "$PG_NAME" '{i:{projectId:$p,environmentId:$e,name:$n,source:{image:"ghcr.io/railwayapp-templates/postgres-ssl:18"}}}')")"
  pgid="$(jq -r '.data.serviceCreate.id // empty' <<<"$r")"; [[ -n "$pgid" ]] || { ev "PG create failed: $(gerr "$r")"; return 1; }
  upsert_vars "$pgid" "$(jq -nc --arg pw "$pw" '{POSTGRES_USER:"postgres",POSTGRES_DB:"railway",POSTGRES_PASSWORD:$pw,PGDATA:"/var/lib/postgresql/data/pgdata",SSL_CERT_DAYS:"30"}')" || return 1
  r="$(gql 'mutation($i:VolumeCreateInput!){volumeCreate(input:$i){id name}}' "$(jq -nc --arg p "$PROJ" --arg e "$ENVID" --arg s "$pgid" '{i:{projectId:$p,environmentId:$e,serviceId:$s,mountPath:"/var/lib/postgresql/data"}}')")"
  [[ -n "$(jq -r '.data.volumeCreate.id // empty' <<<"$r")" ]] || { ev "PG volume failed: $(gerr "$r")"; return 1; }
  ev "postgres service + volume created"
  read -r prevpg _ _ < <(latest_dep "$PG_NAME")
  r="$(gql 'mutation($s:String!,$e:String!){serviceInstanceRedeploy(serviceId:$s,environmentId:$e)}' "$(jq -nc --arg s "$pgid" --arg e "$ENVID" '{s:$s,e:$e}')")"
  [[ -z "$(gerr "$r")" ]] || ev "pg redeploy note: $(gerr "$r")"
  out="$(wait_dep "$PG_NAME" SUCCESS 600 "$prevpg")" || { ev "PG deployment did not reach SUCCESS: $out"; railway logs --service "$PG_NAME" --environment "$ENVID" --lines 40 2>&1 | grep -viE 'password' | tail -25 | tee -a "$EVID"; return 1; }
  ev "postgres deployment SUCCESS at +$(( $(date +%s) - t0 ))s"

  # ---- application service (task phase): created WITHOUT a source, configured, then connected to the pin branch (head == candidate)
  local appid
  r="$(gql 'mutation($i:ServiceCreateInput!){serviceCreate(input:$i){id name}}' "$(jq -nc --arg p "$PROJ" --arg e "$ENVID" --arg n "$APP_NAME" '{i:{projectId:$p,environmentId:$e,name:$n}}')")"
  appid="$(jq -r '.data.serviceCreate.id // empty' <<<"$r")"; [[ -n "$appid" ]] || { ev "app create failed: $(gerr "$r")"; return 1; }
  r="$(gql 'mutation($i:VolumeCreateInput!){volumeCreate(input:$i){id name}}' "$(jq -nc --arg p "$PROJ" --arg e "$ENVID" --arg s "$appid" '{i:{projectId:$p,environmentId:$e,serviceId:$s,mountPath:"/var/lib/simo-os/uploads"}}')")"
  [[ -n "$(jq -r '.data.volumeCreate.id // empty' <<<"$r")" ]] || { ev "uploads volume failed: $(gerr "$r")"; return 1; }
  local dburl="postgresql+psycopg://postgres:${pw}@${PG_NAME}.railway.internal:5432/railway"
  local b64; b64="$(base64 -w0 "$bundle")"; local nbytes; nbytes="$(stat -c %s "$bundle")"; local sha; sha="$(sha256sum "$bundle" | cut -d' ' -f1)"
  local vars; vars="$(jq -nc --arg db "$dburl" --arg jwt "$jwt" --arg sp "$seedpw" --arg nb "$nbytes" --arg sha "$sha" \
    '{APP_ENV:"production",DATABASE_URL:$db,JWT_SECRET_KEY:$jwt,SEED_ADMIN_EMAIL:"recovery-drill@disposable.invalid",SEED_ADMIN_PASSWORD:$sp,SEED_DATA_ENABLED:"false",
      CORS_ALLOWED_ORIGINS:"https://recovery-drill.disposable.invalid",FRONTEND_BASE_URL:"https://recovery-drill.disposable.invalid",PUBLIC_API_BASE_URL:"https://recovery-drill.disposable.invalid",
      RAILWAY_RUN_UID:"0",NEW_ACCOUNT_ADMISSION:"closed",READINESS_TIMEOUT_SECONDS:"2",PORT:"8000",UPLOAD_DIR:"/var/lib/simo-os/uploads",RECOVERY_BUNDLE_BYTES:$nb,RECOVERY_BUNDLE_SHA256:$sha}')"
  upsert_vars "$appid" "$vars" || return 1
  local n=0 off=0 chunk=30000 part
  while (( off < ${#b64} )); do part="$(printf '%s' "${b64:off:chunk}")"; upsert_vars "$appid" "$(jq -nc --arg k "$(printf 'RECOVERY_BUNDLE_PART_%03d' $n)" --arg v "$part" '{($k):$v}')" || return 1; off=$((off+chunk)); n=$((n+1)); done
  ev "bundle delivered as $n variable parts ($nbytes bytes, sha256 ${sha:0:16}...)"
  tar -czf /tmp/restore-src.tgz scripts/qa/qa_recovery_drill.py scripts/recovery/content_fingerprint.py scripts/recovery/matched_bundle.py scripts/recovery/restore_upload_bundle.py scripts/recovery/recovery_safety.py scripts/disposable/restore_from_bundle.py scripts/disposable/bundle_transfer.py
  upsert_vars "$appid" "$(jq -nc --arg v "$(base64 -w0 /tmp/restore-src.tgz)" '{RECOVERY_SRC_B64:$v}')" || return 1
  local task_start='sh -c '"'"'mkdir -p /tmp/rs && echo "$RECOVERY_SRC_B64" | base64 -d | tar -xz -C /tmp/rs && cd /app && RESTORE_SRC=/tmp/rs python /tmp/rs/scripts/disposable/restore_from_bundle.py; echo "DRILL|EXIT rc=$?"; sleep 3000'"'"''
  r="$(gql 'mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$i)}' "$(jq -nc --arg s "$appid" --arg e "$ENVID" --arg c "$task_start" '{s:$s,e:$e,i:{startCommand:$c,healthcheckPath:null}}')")"
  [[ -z "$(gerr "$r")" ]] || { ev "task config refused: $(gerr "$r")"; return 1; }
  r="$(gql 'mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$i)}' "$(jq -nc --arg s "$appid" --arg e "$ENVID" --arg img "$IMAGE_REF" --arg u "$REG_USER" --arg t "$REG_TOKEN" '{s:$s,e:$e,i:{source:{image:$img},registryCredentials:{username:$u,password:$t}}}')")"
  [[ -z "$(gerr "$r")" ]] || { ev "image source refused: $(gerr "$r")"; return 1; }
  r="$(gql 'mutation($s:String!,$e:String!){serviceInstanceDeployV2(serviceId:$s,environmentId:$e)}' "$(jq -nc --arg s "$appid" --arg e "$ENVID" '{s:$s,e:$e}')")"
  depid="$(jq -r '.data.serviceInstanceDeployV2 // empty' <<<"$r")"; [[ -n "$depid" ]] || { ev "task deploy refused: $(gerr "$r")"; return 1; }
  ev "application deploy requested from pinned image $IMAGE_REF; waiting"
  out="$(wait_id "$APP_NAME" "$depid" 1200)" || { ev "app task deployment failed: $out"; return 1; }
  ev "IDENTITY task deployment $depid runs image $IMAGE_REF (built from exactly $EXPECTED_SHA)"
  local dl=$(( $(date +%s) + 1500 )) logs="" rc=1
  while (( $(date +%s) < dl )); do
    logs="$(railway logs "$depid" --deployment --service "$APP_NAME" --environment "$ENVID" --lines 3000 2>&1 | tr -d '\r' | grep -a '^DRILL|' || true)"
    grep -q '^DRILL|EXIT' <<<"$logs" && break
    sleep 15
  done
  printf '%s\n' "$logs" | cut -c1-260 | tee -a "$EVID"
  grep -q '^DRILL|DONE' <<<"$logs" && ! grep -q '^DRILL|FAIL' <<<"$logs" && rc=0
  local t_task=$(date +%s)
  ev "RECOVERY TIMING provision_to_verified_seconds=$(( t_task - t0 ))"

  # ---- phase 2: the restored application under its REAL start command and health check, same commit, restored database + volume
  if [[ $rc -eq 0 ]]; then
    read -r prevapp _ _ < <(latest_dep "$APP_NAME")
    r="$(gql 'mutation($s:String!,$e:String!,$i:ServiceInstanceUpdateInput!){serviceInstanceUpdate(serviceId:$s,environmentId:$e,input:$i)}' "$(jq -nc --arg s "$appid" --arg e "$ENVID" --arg c "$ORIG_START" --arg pd "$ORIG_PREDEPLOY" '{s:$s,e:$e,i:{startCommand:$c,preDeployCommand:[$pd],healthcheckPath:"/health",healthcheckTimeout:300}}')")"
    [[ -z "$(gerr "$r")" ]] || { ev "normal-start config refused: $(gerr "$r")"; rc=1; }
    r="$(gql 'mutation($s:String!,$e:String!){serviceInstanceDeployV2(serviceId:$s,environmentId:$e)}' "$(jq -nc --arg s "$appid" --arg e "$ENVID" '{s:$s,e:$e}')")"
    id2="$(jq -r '.data.serviceInstanceDeployV2 // empty' <<<"$r")"; [[ -n "$id2" ]] || { ev "normal-start redeploy refused: $(gerr "$r")"; rc=1; }
    if [[ -n "${id2:-}" ]]; then
      out="$(wait_id "$APP_NAME" "$id2" 1200)" && ev "PASS restored application under the real start command (migrate_gate at head on the restored database) and /health: deployment $id2 SUCCESS, image $IMAGE_REF" || { ev "FAIL restored application did not become healthy: $out"; rc=1; }
    fi
  fi
  ev "RECOVERY ELAPSED_TOTAL_SECONDS=$(( $(date +%s) - t0 )) (excludes teardown)"
  return $rc
}

case "${1:-}" in
  run) scope_guard; run "$2"; rc=$?; ev "RUN RESULT rc=$rc"; exit $rc ;;
  teardown) scope_guard; teardown ;;
  inventory) scope_guard; inventory ;;
  *) echo "usage: run <bundle.tgz> | teardown | inventory"; exit 64 ;;
esac
