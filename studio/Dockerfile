# Videoproduktion für den YouTube-Kanal — bewusst ein EIGENER Container.
#
# ffmpeg zieht 277 zusätzliche Pakete nach (gemessen 14.09.2026). In Fridays
# schlankem Image hätte das nichts zu suchen: Sie soll sofort antworten, nicht
# ein halbes Multimedia-Studio mitschleppen. Hier darf es schwer sein — dieser
# Container rechnet in Ruhe, während Friday ansprechbar bleibt.
#
# Der Renderer selbst ist das Projekt AutoShorts AI (AI-Youtube-Shorts-Generator),
# das Eike mitgebracht hat: Skript über Gemini, Stimme über edge-tts, Bildmaterial
# als ECHTE Stockaufnahmen von Pexels/Pixabay — nie KI-generiert, damit der Kanal
# monetarisierbar bleibt.
FROM python:3.12-slim

# ffmpeg für den Schnitt, fonts für eingebrannte Untertitel, tini als PID 1.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg tini fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /renderer

# Erst die Abhängigkeiten, dann der Code: So bleibt die Paketschicht beim
# Ändern des Codes im Cache.
COPY renderer/requirements.txt /renderer/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY renderer/ /renderer/

# Der Arbeiter liegt neben dem Renderer und wartet auf Aufträge.
COPY arbeiter.py /arbeiter.py

# Kein eigener Nutzer mit fester ID: Die Ausgabe landet in einem gemounteten
# Ordner, der Friday gehört. Wird beim Compose über `user:` gesetzt.
ENTRYPOINT ["/usr/bin/tini", "--"]

# Dauerdienst mit Auftragsordner — bewusst NICHT „Friday startet je Video einen
# Container". Dafür bräuchte Friday den Docker-Socket, und wer den hat, ist
# faktisch Administrator des ganzen Servers. Stattdessen legt Friday eine
# Auftragsdatei ab, dieser Arbeiter nimmt sie auf und rendert in Ruhe. Keine
# Rechteausweitung, und das Rendern darf so lange dauern, wie es braucht.
CMD ["python", "/arbeiter.py"]
