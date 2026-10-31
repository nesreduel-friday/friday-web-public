#!/usr/bin/env bash
# Handy-Steuerung ein- oder ausschalten. Läuft NUR auf Zuruf (kein cron): solange sie an ist, kann jeder im Tailnet das Handy bedienen.
#
#   handy_steuerung_starten.sh          an:  Dienst in tmux-Sitzung `friday-handy-steuerung` + HTTPS-Freigabe im Tailnet
#   handy_steuerung_starten.sh stopp    aus: Freigabe weg, Dienst beendet (räumt das Handy dabei selbst auf)
#
# Adresse: https://hphome.beispiel.ts.net:8444/   (443 gehört Friday, 8443 der Familien-Standort)
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
cd "$HOME/docker/friday-web" || exit 1
if [ "$1" = "stopp" ]; then
  tailscale serve --https=8444 off 2>/dev/null
  tmux kill-session -t friday-handy-steuerung 2>/dev/null && echo "Dienst beendet." || echo "Dienst lief nicht."
  exit 0
fi
tmux has-session -t friday-handy-steuerung 2>/dev/null || \
  tmux new-session -d -s friday-handy-steuerung -c "$HOME/docker/friday-web" python3 host-helfer/handy_steuerung.py
tailscale serve --bg --https=8444 http://127.0.0.1:8140 >/dev/null && echo "An: https://hphome.beispiel.ts.net:8444/"
