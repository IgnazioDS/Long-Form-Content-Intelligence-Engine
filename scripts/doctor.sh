#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUN_SMOKE=false

usage() {
  cat <<'EOF'
Usage: scripts/doctor.sh [--run-smoke]

  --run-smoke   Run make smoke and make smoke-prod after preflight checks.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --run-smoke) RUN_SMOKE=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; usage; exit 2 ;;
  esac
done

FAILURES=0
WARNINGS=0

pass() { echo "[PASS] $*"; }
warn() { echo "[WARN] $*"; WARNINGS=$((WARNINGS + 1)); }
fail() { echo "[FAIL] $*"; FAILURES=$((FAILURES + 1)); }

array_contains() {
  local needle="$1"
  shift
  for item in "$@"; do
    if [[ "$item" == "$needle" ]]; then
      return 0
    fi
  done
  return 1
}

version_ge() {
  local version="$1"
  local required="$2"
  local v_major v_minor r_major r_minor
  IFS='.' read -r v_major v_minor _ <<< "$version"
  IFS='.' read -r r_major r_minor _ <<< "$required"
  v_minor=${v_minor:-0}
  r_minor=${r_minor:-0}
  if (( v_major > r_major )); then
    return 0
  fi
  if (( v_major == r_major && v_minor >= r_minor )); then
    return 0
  fi
  return 1
}

get_env_value() {
  local key="$1"
  local line value
  line="$(grep -E "^[[:space:]]*${key}=" .env 2>/dev/null | tail -n1 || true)"
  value="${line#*=}"
  value="$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
  printf '%s' "$value"
}

echo "Environment readiness checks"

if command -v git >/dev/null 2>&1; then
  pass "git installed ($(git --version | head -n1))"
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if [[ -z "$(git status --porcelain)" ]]; then
      pass "git status clean"
    else
      warn "git status dirty (uncommitted changes present)"
    fi
  else
    warn "not a git repository (git status skipped)"
  fi
else
  fail "git not installed (install git to clone/build the repo)"
fi

MAKE_AVAILABLE=false
if command -v make >/dev/null 2>&1; then
  pass "make installed ($(make --version | head -n1))"
  MAKE_AVAILABLE=true
else
  fail "make not installed (install GNU Make)"
fi

if command -v curl >/dev/null 2>&1; then
  pass "curl installed ($(curl --version | head -n1))"
else
  fail "curl not installed (install curl for health checks)"
fi

DOCKER_AVAILABLE=false
if command -v docker >/dev/null 2>&1; then
  pass "Docker installed ($(docker --version | head -n1))"
  DOCKER_AVAILABLE=true
else
  fail "Docker not installed (install Docker Desktop or engine)"
fi

DOCKER_COMPOSE_AVAILABLE=false
if [[ "$DOCKER_AVAILABLE" == true ]]; then
  if docker compose version >/dev/null 2>&1; then
    pass "docker compose plugin installed ($(docker compose version | head -n1))"
    DOCKER_COMPOSE_AVAILABLE=true
  else
    if command -v docker-compose >/dev/null 2>&1; then
      fail "docker compose plugin missing (docker-compose found, but make targets use docker compose)"
    else
      fail "docker compose plugin missing (install Docker Compose v2)"
    fi
  fi
fi

if [[ "$DOCKER_AVAILABLE" == true ]]; then
  if docker info >/dev/null 2>&1; then
    pass "Docker daemon running"
  else
    fail "Docker daemon not running or permission denied (start Docker Desktop or fix permissions)"
  fi
fi

host_arch="$(uname -m)"
docker_arch="unknown"
if [[ "$DOCKER_AVAILABLE" == true ]]; then
  docker_arch="$(docker info --format '{{.Architecture}}' 2>/dev/null || echo "unknown")"
fi

compose_platforms=()
while IFS= read -r -d '' compose_file; do
  while IFS= read -r line; do
    platform_value="$(printf '%s' "$line" | sed -E 's/^[[:space:]]*platform:[[:space:]]*//')"
    if [[ -n "$platform_value" ]] && ! array_contains "$platform_value" "${compose_platforms[@]}"; then
      compose_platforms+=("$platform_value")
    fi
  done < <(grep -E '^[[:space:]]*platform:[[:space:]]*' "$compose_file" || true)
done < <(find "$ROOT_DIR" -maxdepth 1 -type f \( -name 'docker-compose*.yml' -o -name 'compose*.yml' \) -print0)

if [[ ${#compose_platforms[@]} -gt 0 ]]; then
  pass "CPU arch host=${host_arch}, docker=${docker_arch}, compose platform overrides: ${compose_platforms[*]}"
else
  pass "CPU arch host=${host_arch}, docker=${docker_arch}, compose platform overrides: none"
fi

if [[ "$host_arch" == "arm64" && "$docker_arch" == "amd64" ]]; then
  warn "Docker is running amd64 on arm64 (expect emulation overhead)"
fi

df_output="$(df -Pk . | tail -n1)"
avail_kb="$(printf '%s' "$df_output" | awk '{print $4}')"
avail_gb=$((avail_kb / 1024 / 1024))
if (( avail_gb < 10 )); then
  warn "Disk space low (${avail_gb}GB available; consider freeing space)"
else
  pass "Disk space OK (${avail_gb}GB available)"
fi

expected_env_keys=()
if [[ -f .env.example ]]; then
  while IFS= read -r key; do
    if [[ -n "$key" ]] && ! array_contains "$key" "${expected_env_keys[@]}"; then
      expected_env_keys+=("$key")
    fi
  done < <(grep -E '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' .env.example \
    | sed -E 's/^[[:space:]]*//' | cut -d= -f1)
  pass ".env.example present"
else
  warn ".env.example missing (cannot validate expected env keys)"
fi

if [[ -f .env ]]; then
  pass ".env present"
  env_keys=()
  while IFS= read -r key; do
    if [[ -n "$key" ]] && ! array_contains "$key" "${env_keys[@]}"; then
      env_keys+=("$key")
    fi
  done < <(grep -E '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' .env \
    | sed -E 's/^[[:space:]]*//' | cut -d= -f1)

  missing_keys=()
  for key in "${expected_env_keys[@]}"; do
    if ! array_contains "$key" "${env_keys[@]}"; then
      missing_keys+=("$key")
    fi
  done
  if [[ ${#missing_keys[@]} -gt 0 ]]; then
    fail ".env missing keys from .env.example: ${missing_keys[*]}"
  else
    pass ".env keys match .env.example"
  fi

  ai_provider="$(get_env_value "AI_PROVIDER")"
  if [[ -z "$ai_provider" ]]; then
    ai_provider="openai"
  fi
  openai_key="$(get_env_value "OPENAI_API_KEY")"
  if [[ "$ai_provider" != "fake" && -z "$openai_key" ]]; then
    fail "OPENAI_API_KEY is empty (set it or use AI_PROVIDER=fake for local smoke)"
  else
    pass "OPENAI_API_KEY present or not required (AI_PROVIDER=${ai_provider})"
  fi

  database_url="$(get_env_value "DATABASE_URL")"
  if [[ -z "$database_url" ]]; then
    fail "DATABASE_URL is empty (set it in .env)"
  else
    pass "DATABASE_URL set"
  fi

  redis_url="$(get_env_value "REDIS_URL")"
  if [[ -z "$redis_url" ]]; then
    fail "REDIS_URL is empty (set it in .env)"
  else
    pass "REDIS_URL set"
  fi

  require_api_key="$(get_env_value "REQUIRE_API_KEY")"
  if [[ "$require_api_key" =~ ^([Tt][Rr][Uu][Ee]|1|yes|YES)$ ]]; then
    api_key="$(get_env_value "API_KEY")"
    if [[ -z "$api_key" ]]; then
      fail "API_KEY is empty but REQUIRE_API_KEY=true (set API_KEY)"
    else
      pass "API_KEY set (REQUIRE_API_KEY=true)"
    fi
  else
    pass "API_KEY check skipped (REQUIRE_API_KEY not true)"
  fi
else
  fail ".env missing (copy .env.example to .env)"
fi

if [[ -f apps/web/.env.local ]]; then
  pass "apps/web/.env.local present"
else
  warn "apps/web/.env.local missing (copy apps/web/.env.local.example for UI dev)"
fi

python_required="not specified"
if [[ -f pyproject.toml ]]; then
  python_required="$(sed -n 's/^requires-python = \"\\([^\"]*\\)\"/\\1/p' pyproject.toml | head -n1)"
  python_required="${python_required:-not specified}"
fi

if command -v python3 >/dev/null 2>&1; then
  py_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
  if [[ "$python_required" == "not specified" ]]; then
    pass "python3 installed (${py_version}; required: not specified)"
  else
    req_version="$(printf '%s' "$python_required" | sed -n 's/.*>=\\([0-9][0-9]*\\.[0-9][0-9]*\\).*/\\1/p')"
    if [[ -n "$req_version" ]]; then
      if version_ge "$py_version" "$req_version"; then
        pass "python3 installed (${py_version}; required: ${python_required})"
      else
        fail "python3 ${py_version} does not satisfy required ${python_required}"
      fi
    else
      pass "python3 installed (${py_version}; required: ${python_required})"
    fi
  fi
else
  fail "python3 not installed (required for smoke/tests; expected: ${python_required})"
fi

node_required="not specified"
if [[ -f apps/web/package.json ]]; then
  node_required="$(sed -n 's/.*\"node\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p' apps/web/package.json | head -n1)"
  node_required="${node_required:-not specified}"
fi

if command -v node >/dev/null 2>&1; then
  node_version="$(node --version | sed 's/^v//')"
  pass "node installed (${node_version}; required: ${node_required})"
else
  warn "node not installed (frontend dev requires Node; required: ${node_required})"
fi

if command -v npm >/dev/null 2>&1; then
  pass "npm installed ($(npm --version))"
else
  warn "npm not installed (frontend dev requires npm)"
fi

ports=()
compose_found=false
while IFS= read -r -d '' compose_file; do
  compose_found=true
  while IFS= read -r line; do
    line="$(printf '%s' "$line" | sed 's/#.*$//')"
    line="${line#*-}"
    line="$(printf '%s' "$line" | tr -d " \"'")"
    line="${line%%/*}"
    if [[ -z "$line" ]]; then
      continue
    fi
    IFS=':' read -r part1 part2 part3 <<< "$line"
    host_port=""
    if [[ -n "$part3" ]]; then
      host_port="$part2"
    elif [[ -n "$part2" ]]; then
      host_port="$part1"
    else
      host_port="$part1"
    fi
    if [[ "$host_port" =~ ^[0-9]{1,5}$ ]]; then
      if ! array_contains "$host_port" "${ports[@]}"; then
        ports+=("$host_port")
      fi
    fi
  done < <(
    awk '
      function indent(line) { match(line, /^[[:space:]]*/); return RLENGTH }
      /^[[:space:]]*ports:[[:space:]]*$/ { in_ports=1; ports_indent=indent($0); next }
      in_ports {
        if ($0 ~ /^[[:space:]]*$/) next
        if (indent($0) <= ports_indent) { in_ports=0; next }
        if ($0 ~ /^[[:space:]]*-[[:space:]]*[0-9]/) print $0
      }
    ' "$compose_file"
  )
done < <(find "$ROOT_DIR" -maxdepth 1 -type f \( -name 'docker-compose*.yml' -o -name 'compose*.yml' \) -print0)

if [[ "$compose_found" == false ]]; then
  warn "No compose files found; port checks skipped"
elif [[ ${#ports[@]} -eq 0 ]]; then
  warn "No ports detected in compose files; port checks skipped"
else
  for port in "${ports[@]}"; do
    if command -v lsof >/dev/null 2>&1; then
      if lsof -iTCP:"$port" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
        warn "Port ${port} already in use (stop the service or change compose ports)"
      else
        pass "Port ${port} available"
      fi
    elif command -v nc >/dev/null 2>&1; then
      if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
        warn "Port ${port} already in use (stop the service or change compose ports)"
      else
        pass "Port ${port} available"
      fi
    elif command -v python3 >/dev/null 2>&1; then
      if python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("127.0.0.1", port))
    sys.exit(0)
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
      then
        warn "Port ${port} already in use (stop the service or change compose ports)"
      else
        pass "Port ${port} available"
      fi
    else
      warn "Port ${port} check skipped (install lsof or nc)"
    fi
  done
fi

if [[ "$RUN_SMOKE" == true ]]; then
  if [[ "$MAKE_AVAILABLE" == true ]]; then
    echo "Running smoke tests"
    set +e
    make smoke
    smoke_status=$?
    make smoke-prod
    smoke_prod_status=$?
    set -e

    if [[ $smoke_status -eq 0 ]]; then
      pass "make smoke succeeded"
    else
      fail "make smoke failed (exit ${smoke_status})"
    fi

    if [[ $smoke_prod_status -eq 0 ]]; then
      pass "make smoke-prod succeeded"
    else
      fail "make smoke-prod failed (exit ${smoke_prod_status})"
    fi
  else
    fail "Skipping smoke tests because make is not installed"
  fi
fi

if [[ $FAILURES -gt 0 ]]; then
  echo "Environment readiness failed with ${FAILURES} failure(s) and ${WARNINGS} warning(s)."
  exit 1
fi

echo "Environment readiness passed with ${WARNINGS} warning(s)."
exit 0
