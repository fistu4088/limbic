#!/usr/bin/env bash
# limbic → Hermes outreach gate.
#
# Use as the `script` of a Hermes cron job in the agent's profile. Hermes
# runs it before waking the agent:
#   - nothing to do      → prints {"wakeAgent": false}  → agent is NOT woken, no tokens spent
#   - a pending intent   → claims it and prints the instruction → agent writes the message,
#                          Hermes delivers it to the job's `deliver` target
#
# Env:
#   LIMBIC_URL         default http://127.0.0.1:8770
#   LIMBIC_AGENT       required, limbic agent id (e.g. aria)
#   LIMBIC_TOKEN_FILE  file with the bearer token
#   LIMBIC_LANG        hu | en (default hu)
#   LIMBIC_PYTHONPATH  where the limbic package lives (if not pip-installed)
set -euo pipefail
: "${LIMBIC_AGENT:?set LIMBIC_AGENT}"
export PYTHONPATH="${LIMBIC_PYTHONPATH:-}${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m limbic outreach "$LIMBIC_AGENT" \
  --url "${LIMBIC_URL:-http://127.0.0.1:8770}" \
  ${LIMBIC_TOKEN_FILE:+--token-file "$LIMBIC_TOKEN_FILE"} \
  --lang "${LIMBIC_LANG:-hu}"
