#!/usr/bin/env bash
# Startet den Claude-Helfer in einer tmux-Sitzung, falls er nicht schon läuft.
# Gedacht für cron: einmal @reboot und alle 5 Minuten als Wächter.
#
#   @reboot      $HOME/docker/friday-web/host-helfer/helfer_starten.sh
#   */5 * * * *  $HOME/docker/friday-web/host-helfer/helfer_starten.sh
#
# Von Hand ansehen:  tmux attach -t friday-helfer   (raus mit Strg+b, dann d)
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
cd "$HOME/docker/friday-web" || exit 1
tmux has-session -t friday-helfer 2>/dev/null && exit 0
exec tmux new-session -d -s friday-helfer -c "$HOME/docker/friday-web" \
  python3 host-helfer/claude_helfer.py
