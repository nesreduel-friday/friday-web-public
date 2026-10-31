FROM python:3.12-slim

# tini als PID 1: `docker stop` greift sofort statt nach 10 s.
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY kern/ kern/
COPY web/ web/
# Der Helfer für Eikes Windows-PC — Friday liefert ihn beim Einrichten aus, ausgeführt wird er dort.
COPY pc-helfer/ pc-helfer/
# Der Helfer für hphome selbst (startet Claude-Code-Sitzungen). Ausgeführt wird er auf
# dem Host, hier liegt er nur für den Test, der seine Regeln prüft.
COPY host-helfer/ host-helfer/
COPY test/ test/
# Erfundene Testszenarien: die Werkbank gibt sie (nie echte Daten) an die OpenCode-Sandbox.
COPY eval/szenarien/ eval/szenarien/
# Nur für den Test, der prüft, was der Studio-Arbeiter auf die Platte legt
# (keine Schlüssel). Reine Standardbibliothek; ausgeführt wird er im eigenen Container.
COPY studio/arbeiter.py studio/arbeiter.py

# uid 1000 = dein Benutzer auf hphome, damit ./data ohne root beschreibbar ist.
RUN useradd -u 1000 -m friday && mkdir -p data && chown -R friday:friday /app
USER friday

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "kern.main:app", "--host", "0.0.0.0", "--port", "8080"]
