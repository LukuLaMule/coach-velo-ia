#!/bin/bash
set -a; [ -f ~/mcp/secrets/coros.env ] && source ~/mcp/secrets/coros.env; set +a
exec ~/mcp/coros-mcp/.venv/bin/coros-mcp serve
