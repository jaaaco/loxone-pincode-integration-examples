#!/usr/bin/env bash
set -euo pipefail

# Example workflow for managing Loxone user PIN codes.
#
# Usage examples:
#   ./pin_workflow.sh resolve
#   ./pin_workflow.sh list-users
#   ./pin_workflow.sh set-pin <uuid> <pin>
#   ./pin_workflow.sh show-user <uuid>
#   ./pin_workflow.sh clear-pin <uuid>
#
# Provide placeholders that should be replaced with your real values.

SERVER_BASE="${SERVER_BASE:-https://dns.loxonecloud.com/YOUR-MINISERVER-SERIAL}"
AUTH="${AUTH:-USERNAME:PASSWORD}"

if [[ -z "${SERVER_BASE}" || -z "${AUTH}" ]]; then
  echo "SERVER_BASE and AUTH must be set" >&2
  exit 1
fi

resolve_target_base() {
  local redirect
  redirect=$(curl -sk -u "${AUTH}" -w '%{redirect_url}' -o /dev/null "${SERVER_BASE}")
  if [[ -z "${redirect}" ]]; then
    echo "Failed to resolve target base" >&2
    exit 1
  fi
  echo "${redirect%/}"
}

require_target_base() {
  if [[ -n "${TARGET_BASE:-}" ]]; then
    echo "${TARGET_BASE%/}"
    return
  fi
  resolve_target_base
}

api_get() {
  local path="$1"
  local target_base
  target_base=$(require_target_base)
  curl -sk -u "${AUTH}" "${target_base}${path}"
}

api_post() {
  local path="$1"
  local target_base
  target_base=$(require_target_base)
  curl -sk -u "${AUTH}" "${target_base}${path}"
}

case "${1:-}" in
  resolve)
    resolve_target_base
    ;;
  list-users)
    api_get "/jdev/sps/getuserlist2"
    ;;
  show-user)
    if [[ $# -lt 2 ]]; then
      echo "Usage: $0 show-user <uuid>" >&2
      exit 1
    fi
    api_get "/jdev/sps/getuser/$2"
    ;;
  set-pin)
    if [[ $# -lt 3 ]]; then
      echo "Usage: $0 set-pin <uuid> <pin>" >&2
      exit 1
    fi
    api_post "/jdev/sps/updateuseraccesscode/$2/$3"
    ;;
  clear-pin)
    if [[ $# -lt 2 ]]; then
      echo "Usage: $0 clear-pin <uuid>" >&2
      exit 1
    fi
    api_post "/jdev/sps/updateuseraccesscode/$2/"
    ;;
  *)
    cat >&2 <<USAGE
Usage: $0 <command> [arguments]

Commands:
  resolve             Resolve the current Miniserver address via CloudDNS
  list-users          Retrieve the list of users (UUID lookup)
  show-user <uuid>    Fetch details for a specific user
  set-pin <uuid> <pin>    Assign or update a PIN for the user
  clear-pin <uuid>    Remove the PIN for the user

Optionally export TARGET_BASE to skip the resolve step after you know the
current Miniserver address.
USAGE
    exit 1
    ;;
esac
