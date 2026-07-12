#!/usr/bin/env bash
# Deploy a blueprint by name and watch it until it finishes.
# Usage:  source env.sh && ./30-deploy.sh centos7-small my-vm-01
set -euo pipefail
cd "$(dirname "$0")"
source ./lib.sh
BP_NAME="${1:?usage: 30-deploy.sh <blueprint-name> <deployment-name>}"
DEP_NAME="${2:?usage: 30-deploy.sh <blueprint-name> <deployment-name>}"
export TOK=$(vra_token)

BP_ID=$(vget "/blueprint/api/blueprints" | sed 's/},{/}\n{/g' | grep -F "\"name\":\"$BP_NAME\"" | grep -oE '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
[ -n "$BP_ID" ] || { echo "blueprint '$BP_NAME' not found"; exit 1; }

DID=$(vpost "/blueprint/api/blueprint-requests" \
  "{\"blueprintId\":\"$BP_ID\",\"deploymentName\":\"$DEP_NAME\",\"projectId\":\"$(cat .project 2>/dev/null | cut -d= -f2)\",\"inputs\":{}}" \
  | grep -oE '"deploymentId":"[^"]*"' | head -1 | cut -d'"' -f4)
echo "deployment $DEP_NAME started (id=$DID)"

while :; do
  st=$(vget "/deployment/api/deployments/$DID" | grep -oE '"status":"[^"]*"' | head -1 | cut -d'"' -f4)
  addr=$(vget "/deployment/api/deployments/$DID?expand=resources" | grep -oE '"address":"[0-9.]+"' | head -1 | cut -d'"' -f4)
  echo "  status=$st addr=${addr:-none}"
  case "$st" in CREATE_SUCCESSFUL) echo "OK -> $addr"; break;; CREATE_FAILED) echo "FAILED"; exit 1;; esac
  sleep 20
done
