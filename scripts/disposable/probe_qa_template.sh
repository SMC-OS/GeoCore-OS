#!/usr/bin/env bash
# READ-ONLY. Describes how the existing isolated-QA PostgreSQL and API services are built (image, commands, volume mounts, variable NAMES)
# so the disposable replacement can match them. Uses the isolated-QA project token for reads only. Prints no variable values and no credentials.
set -uo pipefail
: "${RAILWAY_TOKEN:?missing QA token}"
ENV=f948241d-446f-43ed-bff0-5d12decffd50; PROJ=a16eaf0a-b5a5-4e8e-b79e-4d191457638e
API=40d0cad3-0c01-467a-910e-6c9f818fd2bf; WEB=8aa9dce6-49c9-4da2-b4a7-288de10e0904; PG=fe3ac31d-4b80-4db4-aad5-f9f2c7f83bfd
gql() { curl -sS --max-time 40 https://backboard.railway.com/graphql/v2 -H "Project-Access-Token: $RAILWAY_TOKEN" -H 'Content-Type: application/json' -d "$(jq -nc --arg q "$1" --argjson v "${2:-{\}}" '{query:$q,variables:$v}')"; }
for pair in "postgres:$PG" "api:$API" "web:$WEB"; do
  name="${pair%%:*}"; id="${pair#*:}"
  echo "=== service instance: $name"
  gql 'query($s:String!,$e:String!){ serviceInstance(serviceId:$s, environmentId:$e){ serviceName startCommand preDeployCommand healthcheckPath healthcheckTimeout builder dockerfilePath rootDirectory railwayConfigFile numReplicas restartPolicyType source { image repo } } }' \
    "$(jq -nc --arg s "$id" --arg e "$ENV" '{s:$s,e:$e}')" | jq -c 'if .errors then {errors:[.errors[].message]} else .data.serviceInstance end'
done
echo "=== volumes (mount path and size only)"
gql 'query($p:String!){ project(id:$p){ volumes { edges { node { id name volumeInstances { edges { node { mountPath serviceId environmentId sizeMB } } } } } } } }' \
  "$(jq -nc --arg p "$PROJ" '{p:$p}')" | jq -c 'if .errors then {errors:[.errors[].message]} else [.data.project.volumes.edges[].node | {name, instances:[.volumeInstances.edges[].node | {mountPath, service:(.serviceId|.[0:8]), sizeMB}]}] end'
export RAILWAY_TOKEN
for svc in geocore-pg-qa geocore-api-qa; do :; done
echo "=== variable NAMES per service"
for svc in $(railway status --json 2>/dev/null | jq -r '.environments.edges[]?.node | select(.id=="'"$ENV"'") | .serviceInstances.edges[]?.node.serviceName'); do
  printf '%s: ' "$svc"; railway variable list --service "$svc" --environment "$ENV" --json 2>/dev/null | jq -r 'keys | map(select(startswith("RAILWAY_")|not)) | join(",")'
done
