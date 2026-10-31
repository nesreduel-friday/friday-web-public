"""Gemeinsame HTTP-Helfer für Dienste."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

# Nur der Verbindungsaufbau (ConnectError, ConnectTimeout) wird wiederholt, nie eine schon
# gesendete Anfrage — auch beim Schulmanager-Login unbedenklich. Anlass: „ConnectError“ bei
# Schulmanager (22.09.2026) und Spotify (23.09.2026), sonst stabil.
VERSUCHE = 2


def client(**kwargs) -> httpx.AsyncClient:
    kwargs.setdefault("transport", httpx.AsyncHTTPTransport(retries=VERSUCHE))
    return httpx.AsyncClient(**kwargs)


def nicht_erreichbar(name: str, exc: Exception, zusatz: str = "") -> str:
    """Text fürs Werkzeug — und die Ursache ins Log, denn der Typname allein sagt nicht, ob es
    DNS, Verbindung oder Zeitlimit war."""
    ursache = exc.__cause__ or exc.__context__
    log.warning("%s nicht erreichbar: %r (Ursache: %r)", name, exc, ursache)
    return f"{name} antwortet gerade nicht ({type(exc).__name__}).{zusatz}"
