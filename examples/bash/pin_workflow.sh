#!/usr/bin/env bash
set -euo pipefail

# Example workflow for managing Loxone user PIN codes via CloudDNS.
#
# Two integration models:
#   Model A (static user per door): set-pin / clear-pin
#   Model B (ephemeral user per reservation, in a group): list-groups /
#     create-guest / delete-user
#
# Usage examples:
#   ./pin_workflow.sh resolve
#   ./pin_workflow.sh list-users
#   ./pin_workflow.sh show-user <uuid>
#   ./pin_workflow.sh set-pin <uuid> <pin>
#   ./pin_workflow.sh clear-pin <uuid>
#   ./pin_workflow.sh list-groups
#   ./pin_workflow.sh create-guest <group-uuid> <name> <pin> [from-unix] [until-unix]
#   ./pin_workflow.sh delete-user <uuid>
#
# Provide placeholders that should be replaced with your real values.

SERVER_BASE="${SERVER_BASE:-https://dns.loxonecloud.com/YOUR-MINISERVER-SERIAL}"
AUTH="${AUTH:-USERNAME:PASSWORD}"

# Loxone timestamps count seconds from 2009-01-01 00:00:00 UTC, not the Unix epoch.
LOXONE_EPOCH_OFFSET=1230768000

if [[ -z "${SERVER_BASE}" || -z "${AUTH}" ]]; then
  echo "SERVER_BASE and AUTH must be set" >&2
  exit 1
fi

to_loxone_epoch() {
  echo "$(( $1 - LOXONE_EPOCH_OFFSET ))"
}

# Pure-bash percent-encoding (ASCII payloads only).
urlencode() {
  local s="$1" i c out=""
  for (( i = 0; i < ${#s}; i++ )); do
    c="${s:i:1}"
    case "${c}" in
      [a-zA-Z0-9.~_-]) out+="${c}" ;;
      *) printf -v c '%%%02X' "'${c}"; out+="${c}" ;;
    esac
  done
  printf '%s' "${out}"
}

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
  list-groups)
    api_get "/jdev/sps/getgrouplist"
    ;;
  create-guest)
    if [[ $# -lt 4 ]]; then
      echo "Usage: $0 create-guest <group-uuid> <name> <pin> [from-unix] [until-unix]" >&2
      exit 1
    fi
    group="$2"; name="$3"; pin="$4"
    from_unix="${5:-$(date +%s)}"
    until_unix="${6:-$(( from_unix + 86400 ))}"
    valid_from=$(to_loxone_epoch "${from_unix}")
    valid_until=$(to_loxone_epoch "${until_unix}")
    json='{"name":"'"${name}"'","userState":4,"usergroups":["'"${group}"'"],"validFrom":'"${valid_from}"',"validUntil":'"${valid_until}"',"expirationAction":1,"keycodes":[{"code":"'"${pin}"'"}]}'
    api_post "/jdev/sps/addoredituser/$(urlencode "${json}")"
    ;;
  delete-user)
    if [[ $# -lt 2 ]]; then
      echo "Usage: $0 delete-user <uuid>" >&2
      exit 1
    fi
    api_post "/jdev/sps/deleteuser/$2"
    ;;
  *)
    cat >&2 <<USAGE
Usage: $0 <command> [arguments]

Model A — static user per door:
  resolve                 Resolve the current Miniserver address via CloudDNS
  list-users              Retrieve the list of users (UUID lookup)
  show-user <uuid>        Fetch details for a specific user
  set-pin <uuid> <pin>    Assign or update a PIN for the user
  clear-pin <uuid>        Remove the PIN for the user

Model B — ephemeral user per reservation, in a group:
  list-groups                                   List access groups (UUID lookup)
  create-guest <group-uuid> <name> <pin> [from-unix] [until-unix]
                                                Create a guest user in a group,
                                                with PIN and validity window
  delete-user <uuid>                            Delete a user

from-unix / until-unix are Unix seconds (e.g. \$(date +%s)); the script converts
them to Loxone epoch. Defaults: from = now, until = from + 24h.

Optionally export TARGET_BASE to skip the resolve step after you know the
current Miniserver address.
USAGE
    exit 1
    ;;
esac
