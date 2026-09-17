#!/bin/bash
# Re-pousse les seances sur le compteur iGPSport.
#
# Passe-plat : charge les identifiants et la FTP depuis les secrets, puis
# lance push_program.py avec le python du MCP iGPSport. Le coach du matin
# l'appelle avec --si-manquant, qui ne pousse que si des seances ont disparu
# de l'appli (et ne voit donc pas un changement de FTP : apres un retest,
# relancer sans option).
#
# PUSH_PROGRAM pointe ailleurs si le depot n'est pas clone dans ~/coach-velo-ia.
set -euo pipefail
PUSH_PROGRAM="${PUSH_PROGRAM:-$HOME/coach-velo-ia/mcp/push_program.py}"
set -a; . "$HOME/mcp/secrets/igpsport.env"; set +a
exec "$HOME/mcp/igpsport-mcp/.venv/bin/python" "$PUSH_PROGRAM" "$@"
