#!/usr/bin/env bash
# Startet den Schlüssel-Helfer in einer tmux-Sitzung, falls er nicht schon läuft.
# Gedacht für cron: einmal @reboot und alle 5 Minuten als Wächter.
#
#   @reboot      $HOME/docker/friday-web/host-helfer/schluessel_helfer_starten.sh
#   */5 * * * *  $HOME/docker/friday-web/host-helfer/schluessel_helfer_starten.sh
#
# Von Hand ansehen:  tmux attach -t friday-schluessel-helfer   (raus mit Strg+b, dann d)
export PATH="$HOME/.local/google-cloud-sdk/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
cd "$HOME/docker/friday-web" || exit 1
tmux has-session -t friday-schluessel-helfer 2>/dev/null && exit 0
exec tmux new-session -d -s friday-schluessel-helfer -c "$HOME/docker/friday-web" \
  python3 host-helfer/schluessel_helfer.py
