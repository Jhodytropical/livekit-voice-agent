#!/usr/bin/env bash
# Publish gate. Run from the repo root: bash scripts/publish.sh
# Refuses to initialise or commit if anything that looks like a secret is staged.
set -euo pipefail

# Initialise first, so the secret scan below can use --exclude-standard and
# therefore honour .gitignore. Scanning before `git init` fell back to a raw
# find(1) that swept logs/ and .env.local and refused to publish a clean repo.
echo "== 0. git init (no commit yet) =="
[ -d .git ] || git init -b main
echo "   ok"

echo "== 1. secret scan on exactly the files that would be committed =="
FILES=$(git ls-files --others --cached --exclude-standard)

HITS=$(echo "$FILES" | xargs grep -lE \
  "sk-[A-Za-z0-9_-]{20,}|wss://[a-z0-9-]+\.livekit\.cloud|APIKey[A-Za-z0-9]{10,}|[A-Za-z0-9]{40,}" \
  2>/dev/null | grep -v "requirements.lock.txt" | grep -v ".env.example" || true)

if [ -n "$HITS" ]; then
  echo "REFUSING TO PUBLISH — possible secrets in:"; echo "$HITS"; exit 1
fi
echo "   clean"

echo "== 2. .env.local must not exist as a tracked file =="
if [ -f .env.local ] && git check-ignore -q .env.local 2>/dev/null; then
  echo "   .env.local present and correctly ignored"
elif [ -f .env.local ]; then
  echo "REFUSING — .env.local exists but is NOT ignored"; exit 1
else
  echo "   no .env.local"
fi

echo "== 3. tests must pass =="
./.venv/bin/python -m pytest -q

echo "== 4. commit =="
git add -A
git status --short
echo
echo "Review the list above. If it contains anything you would not put on the public"
echo "internet, Ctrl-C now."
read -r -p "Commit and continue? [y/N] " ok
[ "$ok" = "y" ] || { echo "stopped"; exit 1; }

git commit -m "LiveKit inbound appointment agent: idempotent tool runtime, barge-in config, live-verified date anchoring"

echo
echo "== 5. push =="
echo "Create an EMPTY public repo on GitHub named 'livekit-voice-agent' (no README,"
echo "no .gitignore, no licence), then run:"
echo
echo "  git remote add origin https://github.com/<you>/livekit-voice-agent.git"
echo "  git push -u origin main"
