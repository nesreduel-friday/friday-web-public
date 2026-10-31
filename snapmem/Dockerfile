# Snapchat-Memories zusammenführen und filtern — EIGENER Container (ffmpeg + OpenCV
# gehören nicht ins schlanke Friday-Abbild). Aufträge kommen als Dateien über /snapmem.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

COPY snapmem/ snapmem/
COPY models/ models/
COPY tests/ tests/

RUN useradd -u 1000 -m snap && chown -R snap:snap /app
USER snap
ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "snapmem.arbeiter"]
