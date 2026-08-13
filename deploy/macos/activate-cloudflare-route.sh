#!/bin/bash

set -euo pipefail

cat >&2 <<'EOF'
activate-cloudflare-route.sh is retired.

The D3 helper is retained as historical deployment evidence, but Treasury no longer owns or
changes the shared Cloudflare tunnel. Validate, dry-run, activate, and roll back shared ingress
through the neutral host runbook and scripts under:

  https://github.com/chranama/web-server-infrastructure
  /Users/chranama-server/web-server-infrastructure-runtime/installed/

No file, process, route, or service was changed.
EOF
exit 64
