#!/bin/bash
set -euo pipefail

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH

APP_ROOT="/Users/chranama-server/treasury-takehome"
DATA_ROOT="/Users/chranama-server/treasury-takehome-data"
PORT=8081

failures=0
check_executable() {
  path=$1
  if [ -x "$path" ]; then
    printf 'ok executable %s\n' "$path"
  else
    printf 'missing executable %s\n' "$path"
    failures=$((failures + 1))
  fi
}

printf 'user=%s group=%s\n' "$(/usr/bin/id -un)" "$(/usr/bin/id -gn)"
printf 'os=%s version=%s architecture=%s\n' \
  "$(/usr/bin/sw_vers -productName)" \
  "$(/usr/bin/sw_vers -productVersion)" \
  "$(/usr/bin/uname -m)"
printf 'memory_bytes=%s\n' "$(/usr/sbin/sysctl -n hw.memsize)"
/bin/df -h /Users
/bin/ls -l /etc/localtime
printf 'utc_now=%s\n' "$(TZ=UTC /bin/date '+%Y-%m-%dT%H:%M:%SZ')"

[ "$(/usr/bin/uname -s)" = "Darwin" ] || {
  printf 'unsupported operating system\n'
  failures=$((failures + 1))
}
[ "$(/usr/bin/id -un)" = "chranama-server" ] || {
  printf 'unexpected deployment user\n'
  failures=$((failures + 1))
}
[ "$(/usr/bin/id -gn)" = "staff" ] || {
  printf 'unexpected deployment group\n'
  failures=$((failures + 1))
}
[ "$(/usr/bin/uname -m)" = "x86_64" ] || {
  printf 'unexpected deployment architecture\n'
  failures=$((failures + 1))
}

check_executable /usr/bin/git
check_executable /usr/bin/curl
check_executable /bin/launchctl
check_executable /usr/sbin/lsof
check_executable /usr/sbin/newsyslog
check_executable /usr/local/bin/uv
check_executable /usr/local/bin/cloudflared

printf 'uv=%s\n' "$(/usr/local/bin/uv --version)"
printf 'cloudflared=%s\n' "$(/usr/local/bin/cloudflared --version)"
if /usr/local/bin/uv python find 3.12 >/dev/null 2>&1; then
  printf 'python_3_12=installed\n'
else
  printf 'python_3_12=not_installed_install_during_D1\n'
fi

if /usr/sbin/lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  printf 'port_%s=in_use\n' "$PORT"
  failures=$((failures + 1))
else
  printf 'port_%s=available\n' "$PORT"
fi
if /usr/sbin/lsof -nP -iTCP:8080 -sTCP:LISTEN >/dev/null 2>&1; then
  printf 'mealcheck_port_8080=in_use_expected\n'
else
  printf 'mealcheck_port_8080=not_listening\n'
  failures=$((failures + 1))
fi

for path in "$APP_ROOT" "$DATA_ROOT"; do
  if [ -e "$path" ]; then
    printf 'path=%s state=exists\n' "$path"
  else
    printf 'path=%s state=available\n' "$path"
  fi
done

if /bin/launchctl print system/dev.mealcheck.tunnel >/dev/null 2>&1; then
  printf 'mealcheck_tunnel=running\n'
else
  printf 'mealcheck_tunnel=not_running\n'
  failures=$((failures + 1))
fi

tunnel_config="$HOME/.cloudflared/mealcheck-api.yml"
if [ -f "$tunnel_config" ]; then
  printf 'tunnel_config=%s\n' "$tunnel_config"
  /usr/bin/awk '/^[[:space:]-]*(hostname:|service:)/ {print "tunnel_" $0}' "$tunnel_config"
else
  printf 'tunnel_config=missing\n'
  failures=$((failures + 1))
fi

openai_status=$(
  /usr/bin/curl --silent --output /dev/null --write-out '%{http_code}' \
    --connect-timeout 5 --max-time 10 https://api.openai.com/v1/models || true
)
case "$openai_status" in
  200|401|403) printf 'openai_https=reachable status=%s\n' "$openai_status" ;;
  *)
    printf 'openai_https=unreachable status=%s\n' "${openai_status:-none}"
    failures=$((failures + 1))
    ;;
esac

if [ "$failures" -ne 0 ]; then
  printf 'preflight=failed failures=%s\n' "$failures"
  exit 1
fi
printf 'preflight=passed\n'
