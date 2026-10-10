#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s <host-port> <compose-project>\n' "$0" >&2
  exit 64
}

[[ $# -eq 2 ]] || usage
port=$1
project=$2

[[ $port =~ ^[0-9]{1,5}$ ]] || usage
(( port >= 1 && port <= 65535 )) || usage
[[ $project =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || usage

command -v docker >/dev/null 2>&1 || {
  printf 'Cannot check port %s: docker is unavailable.\n' "$port" >&2
  exit 69
}
command -v ss >/dev/null 2>&1 || {
  printf 'Cannot check port %s: ss is unavailable.\n' "$port" >&2
  exit 69
}

published=$(docker ps -a --filter "publish=$port" \
  --format '{{.Names}}|{{.State}}|{{.Label "com.docker.compose.project"}}|{{.Ports}}')
other_projects=()
same_project=0

while IFS='|' read -r name state owner bindings; do
  [[ -n ${name:-} ]] || continue
  if [[ ${owner:-} == "$project" ]]; then
    if [[ $state == running && $bindings == *"127.0.0.1:$port->8080/tcp"* ]]; then
      same_project=1
    elif [[ $state == running ]]; then
      other_projects+=("${name}:unexpected-binding")
    fi
  else
    other_projects+=("${name}:${owner:-unlabelled}")
  fi
done <<< "$published"

if ((${#other_projects[@]} > 0)); then
  printf 'Port %s conflicts with Docker container(s): %s\n' \
    "$port" "${other_projects[*]}" >&2
  exit 1
fi

tcp_listeners=$(ss -H -ltn "sport = :$port")
udp_listeners=$(ss -H -lun "sport = :$port")
if [[ -n $udp_listeners ]]; then
  printf 'Port %s has an unexpected UDP listener.\n' "$port" >&2
  exit 1
fi
if [[ -n $tcp_listeners ]]; then
  addresses=$(awk '{print $4}' <<< "$tcp_listeners")
  if (( same_project == 0 )) || [[ $addresses != "127.0.0.1:$port" ]]; then
    printf 'Port %s already has a listening socket outside Compose project %s.\n' \
      "$port" "$project" >&2
    exit 1
  fi
fi

printf 'Port %s is available for Compose project %s.\n' "$port" "$project"
