# Werkbank-Sandbox: führt `opencode run` aus, ohne Geheimnisse und ohne Friday-Daten.
#
# ACHTUNG: Dieses Image installiert `opencode` (neue Abhängigkeit) — vor dem ersten Bauen
# von Eike bestätigen lassen. Nicht bauen, bis das geklärt ist.
#
# Dasselbe Image dient auch dem Egress-Proxy `werkbank-proxy` (Compose überschreibt nur den Befehl).
#
# Installationsweg: npm-Paket `opencode-ai`, festgepinnt auf 1.19.4 (dieselbe Version wie die
# Host-Installation per snap). Das Paket bringt die Binärdatei für Linux mit.
FROM node:22-slim

ENV OPENCODE_VERSION=1.19.4
RUN apt-get update && apt-get install -y --no-install-recommends python3 tini ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g "opencode-ai@${OPENCODE_VERSION}" \
    && npm cache clean --force

COPY runner.py proxy.py /opt/

# Nicht-root (uid 1000 = Benutzer `node` des Basis-Images, wie Eike auf hphome); HOME liegt im
# tmpfs (Compose), damit das Wurzeldateisystem schreibgeschützt bleiben kann.
USER 1000:1000
ENV HOME=/home/sandbox PYTHONUNBUFFERED=1
WORKDIR /tmp

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/opt/runner.py"]
