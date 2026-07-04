#!/usr/bin/env bash
#
# update-surf-report.sh — the one script cron calls.
#
# Regenerates the Playa Remanso surf report and publishes it to GitHub Pages.
# Built to survive cron's stripped-down environment: it resolves its own repo
# path, rebuilds PATH so python3 / git / node / claude are all reachable, logs
# everything with timestamps, and only commits when the report actually changed.
#
# Cron (run as the `joey` user so git credentials + claude auth are available):
#   0 6,14 * * *  /home/joey/repos/surf-trip/scripts/update-surf-report.sh
#
# NOTE: run it as your own user, NOT root — git push uses your stored GitHub
# credentials and the Dude narration uses your ~/.claude login.

set -euo pipefail

# ── Resolve repo root from this script's location (cwd-independent) ──────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Rebuild PATH for cron's minimal environment ─────────────────────────────
# cron typically only gives you /usr/bin:/bin, so add the spots our tools live.
HOME="${HOME:-/home/joey}"
# newest nvm node bin dir (claude is a node app and needs node on PATH)
NODE_BIN="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -n1 || true)"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOME/.local/bin${NODE_BIN:+:$NODE_BIN}"

# ── Logging ─────────────────────────────────────────────────────────────────
LOG="$SCRIPT_DIR/surf-report.log"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*" | tee -a "$LOG"; }

cd "$REPO_DIR"

log "──────── surf report run ────────"
log "repo: $REPO_DIR"

# ── Sanity: required tools present? ─────────────────────────────────────────
for tool in python3 git; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    log "FATAL: '$tool' not found on PATH ($PATH)"
    exit 1
  fi
done
if command -v claude >/dev/null 2>&1; then
  log "claude found ($(command -v claude)) — Dude narration enabled"
else
  log "note: claude not on PATH — report will use the rule-based blurb"
fi

# ── Generate the report ─────────────────────────────────────────────────────
if python3 "$SCRIPT_DIR/generate_surf_report.py" >>"$LOG" 2>&1; then
  log "generator finished OK"
else
  rc=$?
  log "FATAL: generator failed (exit $rc) — see log above"
  exit "$rc"
fi

# ── Publish only if the report changed ──────────────────────────────────────
REPORT="site/data/surf-report.json"
if git diff --quiet -- "$REPORT"; then
  log "no change in report — nothing to publish"
  log "done."
  exit 0
fi

git add "$REPORT"
git commit -q \
  -m "surf: auto-update report ($(date '+%Y-%m-%d %H:%M %Z'))" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
log "committed updated report"

if git push -q origin main; then
  log "pushed to origin/main — GitHub Pages will redeploy shortly"
else
  log "WARN: git push failed (change is committed locally). Check network / GitHub creds."
fi

log "done."
