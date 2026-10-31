#!/usr/bin/env bash
# Probelauf der Garantie-Annahme mit dem echten Gemini (scripts/garantie_probe.py) im Wegwerf-Container — Temp-Daten, Attrappen, nie ./data.
# Eigene Temp-Daten (nie ./data), keine Hintergrundläufe, Quellcode nur lesend eingebunden, Netz nur für Gemini
# (der Container hat ein Netz, der Harness sperrt jede andere Namensauflösung; Werkzeuge sind durch eine Attrappe ersetzt).
# Schlüssel: der Hintergrund-/Eval-Schlüssel des Hosts, NIE Eikes Chat-Hauptschlüssel und nie die letzten 2 Pool-Schlüssel
# (Reserve, wie `config.hintergrund_schluessel_liste`). Er wird nur gelesen, per `-e NAME` (ohne Wert in der Prozessliste)
# durchgereicht und nirgends ausgegeben.
#   scripts/eval-isoliert.sh --pruefen
#   scripts/eval-isoliert.sh --n 3
#   scripts/eval-isoliert.sh --id smalltalk_witz --n 1
# Ergebnis: eval/ergebnisse/eval-<Zeit>.json (außerhalb von data/, per .gitignore nicht im Repo).
P="$(cd "$(dirname "$0")/.." && pwd)"
D="$(mktemp -d)"; chmod 777 "$D"
E="$(mktemp -d)"; chmod 777 "$E"
trap 'rm -rf "$D" "$E"' EXIT
if [[ " $* " != *" --pruefen "* ]]; then
  # Bis zu zwei Schlüssel aus der Hintergrund-Liste (HINTERGRUND, dann GRAPHITI, dann Pool ohne die letzten 2) — nur Werte, kein Echo.
  eval "$(python3 - "$P" <<'PY'
import json, shlex, sys
p = sys.argv[1]
env = {}
try:
    for z in open(p + "/.env", encoding="utf-8"):
        z = z.strip()
        if z and not z.startswith("#") and "=" in z:
            k, v = z.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
except OSError:
    pass
try:
    pool = [e["wert"] for e in json.load(open(p + "/data/gemini_pool.json", encoding="utf-8")).get("schluessel", [])]
except (OSError, ValueError, KeyError):
    pool = []
liste = [env.get("HINTERGRUND_GEMINI_API_KEY", ""), env.get("GRAPHITI_GEMINI_API_KEY", "")]
liste = list(dict.fromkeys(s for s in liste if s))
if len(pool) > 2:
    liste += pool[:-2]
liste = list(dict.fromkeys(s for s in liste if s))
# Eikes Hauptschlüssel und die letzten 2 Pool-Schlüssel sind tabu.
tabu = {env.get("GEMINI_API_KEY", ""), *pool[-2:]}
liste = [s for s in liste if s not in tabu]
if liste:
    print("export GEMINI_API_KEY=" + shlex.quote(liste[0]))
if len(liste) > 1:
    print("export RESERVE_GEMINI_API_KEY=" + shlex.quote(liste[1]))
PY
)"
  [[ -n "${GEMINI_API_KEY:-}" ]] || { echo "Kein Hintergrund-/Eval-Schlüssel gefunden — Abbruch." >&2; exit 2; }
  KEYS=(-e GEMINI_API_KEY -e RESERVE_GEMINI_API_KEY)
else
  KEYS=()
fi
docker run --rm -e FRIDAY_HINTERGRUND=aus -e FRIDAY_DATEN=/tmpdaten -e FRIDAY_EVAL_AUSGABE=/ergebnisse \
  "${KEYS[@]}" -v "$D:/tmpdaten" -v "$E:/ergebnisse" \
  -v "$P/kern:/app/kern:ro" -v "$P/web:/app/web:ro" -v "$P/host-helfer:/app/host-helfer:ro" -v "$P/pc-helfer:/app/pc-helfer:ro" \
  -v "$P/scripts:/app/scripts:ro" -v "$P/eval/szenarien:/app/eval/szenarien:ro" \
  "${IMG:-friday-web:local}" python /app/scripts/garantie_probe.py "$@"
RC=$?


exit $RC
