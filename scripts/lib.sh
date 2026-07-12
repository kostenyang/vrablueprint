#!/usr/bin/env bash
# Shared helpers: obtain a vRA IaaS bearer token and small curl wrappers.
# Requires env.sh to be sourced first (see env.example.sh).
set -euo pipefail

vra_token() {
  local rt tok
  # 1) exchange username/password for a CSP refresh token
  rt=$(curl -k -s --max-time 30 -X POST "$VRA_URL/csp/gateway/am/api/login?access_token" \
        -H 'Content-Type: application/json' \
        -d "{\"username\":\"$VRA_USER\",\"password\":\"$VRA_PASS\",\"domain\":\"$VRA_DOMAIN\"}" \
        | grep -oE '"refresh_token":"[^"]*"' | cut -d'"' -f4)
  # 2) exchange the refresh token for an IaaS bearer token
  tok=$(curl -k -s --max-time 30 -X POST "$VRA_URL/iaas/api/login" \
        -H 'Content-Type: application/json' -d "{\"refreshToken\":\"$rt\"}" \
        | grep -oE '"token":"[^"]*"' | cut -d'"' -f4)
  echo "$tok"
}

# vget PATH ; vpost PATH JSON ; vpatch PATH JSON     (require $TOK exported)
vget()  { curl -k -s --max-time 60 -H "Authorization: Bearer $TOK" "$VRA_URL$1"; }
vpost() { curl -k -s --max-time 90 -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -X POST  "$VRA_URL$1" -d "$2"; }
vpatch(){ curl -k -s --max-time 60 -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -X PATCH "$VRA_URL$1" -d "$2"; }
