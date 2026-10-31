#!/usr/bin/env bash
# Tests im Wegwerf-Container: eigene Temp-Daten (nie ./data), ohne Netz, Quellcode aus dem
# Arbeitsbaum nur lesend eingebunden — kein --build nötig.
#   scripts/test-isoliert.sh test.test_hphome -v
#   scripts/test-isoliert.sh discover -s test -t .
# QUELLE=<Ordner> nimmt den Code aus einem anderen Arbeitsbaum (Werkbank: Skript aus dem
# vertrauten Repo, Code aus dem Worktree); TEST_NAME gibt dem Container einen festen Namen.
P="${QUELLE:-$(cd "$(dirname "$0")/.." && pwd)}"
D="$(mktemp -d)"; chmod 777 "$D"
trap 'rm -rf "$D"' EXIT
docker run --rm ${TEST_NAME:+--name "$TEST_NAME"} --network none -e FRIDAY_HINTERGRUND=aus -e FRIDAY_DATEN=/tmpdaten -v "$D:/tmpdaten" \
  -v "$P/kern:/app/kern:ro" -v "$P/web:/app/web:ro" -v "$P/test:/app/test:ro" \
  -v "$P/host-helfer:/app/host-helfer:ro" -v "$P/pc-helfer:/app/pc-helfer:ro" \
  -v "$P/scripts:/app/scripts:ro" -v "$P/eval:/app/eval:ro" \
  -v "$P/.githooks:/app/.githooks:ro" -v "$P/.gitignore:/app/.gitignore:ro" \
  -v "$P/docker-compose.yml:/app/docker-compose.yml:ro" -v "$P/werkbank-sandbox:/app/werkbank-sandbox:ro" -v "$P/graphiti:/app/graphiti:ro" \
  "${IMG:-friday-web:local}" python -m unittest "$@"
