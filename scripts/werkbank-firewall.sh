#!/usr/bin/env bash
# Sperrt das interne Sandbox-Netz der Werkbank gegen den Host selbst (Bridge-Gateway: SSH, Port 80 ...).
# Docker-intern (`internal: true`) verhindert den Weg ins Internet, nicht den Weg zum Host über die Gateway-Adresse.
# Der Verkehr zum Proxy bleibt erlaubt: Container-zu-Container läuft über die Bridge, nicht durch INPUT.
# Aufruf (als root, idempotent):  sudo scripts/werkbank-firewall.sh [an|aus|status]
# Gilt, bis das Netz neu angelegt wird (andere Bridge) oder der Host neu startet - dann erneut ausführen
# (nach `docker compose --profile werkbank up -d` oder per @reboot-Cron von root).
set -euo pipefail
NETZ="${WERKBANK_NETZ:-friday-web_werkbank-sandbox}"
AKTION="${1:-an}"
ID="$(docker network inspect "$NETZ" --format '{{.Id}}')"
BR="br-${ID:0:12}"
REGEL=(INPUT -i "$BR" -m comment --comment friday-werkbank-sandbox -j DROP)
case "$AKTION" in
  an)     iptables -C "${REGEL[@]}" 2>/dev/null || iptables -I "${REGEL[@]}"; echo "Sperre aktiv auf $BR" ;;
  aus)    iptables -C "${REGEL[@]}" 2>/dev/null && iptables -D "${REGEL[@]}"; echo "Sperre entfernt von $BR" ;;
  status) iptables -C "${REGEL[@]}" 2>/dev/null && echo "aktiv ($BR)" || echo "NICHT aktiv ($BR)" ;;
  *) echo "an|aus|status"; exit 2 ;;
esac
