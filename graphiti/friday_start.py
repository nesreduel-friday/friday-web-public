"""Startet den offiziellen Graphiti-MCP-Server — mit einer Korrektur.

Graphiti legt seinen MCP-Server beim Import mit dem Voreinstellungs-Host
127.0.0.1 an (src/graphiti_mcp_server.py, `mcp = FastMCP(...)`). Genau dann
schaltet MCP 1.29 den DNS-Rebinding-Schutz automatisch ein, mit "localhost"
als einzigem erlaubten Namen. Erst danach stellt Graphiti auf 0.0.0.0 um.
Ergebnis: der Server lauscht im ganzen Docker-Netz, weist aber jeden ab, der
ihn unter seinem Dienstnamen anspricht ("Invalid Host header: graphiti:8000",
HTTP 421) — also genau Friday.

Hier wird der Schutz NICHT abgeschaltet, nur um den Dienstnamen ergänzt,
bevor die Web-App gebaut wird. Der Server selbst läuft unverändert.
Nach außen ist der Container ohnehin nicht erreichbar (keine Ports).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

import graphiti_mcp_server as server  # noqa: E402

server.mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["graphiti:*", "localhost:*", "127.0.0.1:*"],
    allowed_origins=["http://graphiti:*", "http://localhost:*", "http://127.0.0.1:*"],
)

# ---------- Zweite Korrektur: fest eingebaute Modelle ----------
# graphiti-core nimmt für "einfache" Aufgaben und fürs Nachsortieren fest
# eingebaute Modelle (gemini_client.DEFAULT_SMALL_MODEL,
# gemini_reranker_client.DEFAULT_MODEL, beide "gemini-2.5-flash-lite"). Die
# Werksfabrik des MCP-Servers reicht MODEL_NAME für Gemini nicht bis dorthin
# durch (für OpenAI schon). Gemessen am 11.09.2026: "gemini-2.5-flash-lite is
# no longer available to new users" — obwohl es in der Modell-Liste stand.
# Beide Werte werden erst beim Aufruf gelesen, deshalb reicht es, sie hier
# umzustellen.
#
# Alles ist mit hasattr abgesichert: Kennt das Image die Namen nicht, startet der
# Server trotzdem — nur ohne Deutsch-Anhang und Zähler.
import logging  # noqa: E402
import os  # noqa: E402

DEUTSCH = ("\n\nWICHTIG: Schreibe alle natürlichsprachlichen Werte — Fakten, Zusammenfassungen, "
           "Beschreibungen, Namen von Dingen und Themen — auf Deutsch. Eigennamen von Personen und Orten "
           "bleiben, wie sie sind. JSON-Struktur und Feldnamen bleiben unverändert.")

zaehler = logging.getLogger("friday.zaehler")


def _deutsch_anhang(messages):
    if messages and getattr(messages[0], "role", "") == "system" and DEUTSCH not in messages[0].content:
        messages[0].content = messages[0].content + DEUTSCH
    return messages


import graphiti_core.cross_encoder.gemini_reranker_client as reranker  # noqa: E402
import graphiti_core.llm_client.gemini_client as gemini_client  # noqa: E402

KLEINES_MODELL = os.getenv("FRIDAY_KLEINES_MODELL") or os.getenv("MODEL_NAME") or "gemini-3.5-flash-lite"
gemini_client.DEFAULT_SMALL_MODEL = KLEINES_MODELL
reranker.DEFAULT_MODEL = KLEINES_MODELL

# ---------- Zähler: jeder Gemini-Aufruf eine Zeile im Log ----------
# Das Google-SDK protokolliert seine Anfragen nicht. Um zu sehen, was eine
# Episode am Kontingent kostet, wird hier jeder Aufruf gezählt — ganz unten an
# der Bibliothek, damit keiner durchrutscht, egal welcher Graphiti-Teil ihn
# auslöst.
from google.genai import models as genai_models  # noqa: E402

# ---------- Drosselung: das Minutenlimit der kostenlosen Stufe ----------
# gemini-3.5-flash-lite erlaubt 15 Anfragen je Minute und Projekt (gemessen 05.10.2026,
# GenerateRequestsPerMinutePerProjectPerModel-FreeTier). Graphiti schickt pro Episode ein Bündel von
# Aufrufen auf einmal; bei Überschreitung bricht die ganze Episode ab ("Rate limit exceeded").
# Darum: höchstens FRIDAY_GEMINI_AUFRUFE_PRO_MINUTE Aufrufe je Minute (Standard 12, gleichmäßig verteilt) und bei
# einem 429 wegen des Minutenlimits die genannte Wartezeit abwarten und den Aufruf wiederholen.
import asyncio  # noqa: E402
import time  # noqa: E402

_ABSTAND_S = 60.0 / max(int(os.getenv("FRIDAY_GEMINI_AUFRUFE_PRO_MINUTE", "12") or 12), 1)
_naechster_start = 0.0
_takt_sperre = asyncio.Lock()


async def _drosseln():
    global _naechster_start
    async with _takt_sperre:
        jetzt = time.monotonic()
        warte = _naechster_start - jetzt
        _naechster_start = max(jetzt, _naechster_start) + _ABSTAND_S
    if warte > 0:
        await asyncio.sleep(warte)


def _minutenlimit(exc) -> bool:
    text = str(exc)
    return "429" in text and "PerMinute" in text.replace(" ", "")


def _mitzaehlen(art, original):
    async def gezaehlt(self, *args, **kwargs):
        if art != "text":
            zaehler.info("GEMINI-AUFRUF %s %s", art, kwargs.get("model", "?"))
            return await original(self, *args, **kwargs)
        for versuch in range(3):
            await _drosseln()
            zaehler.info("GEMINI-AUFRUF %s %s", art, kwargs.get("model", "?"))
            try:
                return await original(self, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — nur das Minutenlimit wird abgefangen
                if versuch == 2 or not _minutenlimit(exc):
                    raise
                zaehler.info("MINUTENLIMIT, warte 45 s und wiederhole (Versuch %s)", versuch + 2)
                await asyncio.sleep(45)
    return gezaehlt

genai_models.AsyncModels.generate_content = _mitzaehlen("text", genai_models.AsyncModels.generate_content)
genai_models.AsyncModels.embed_content = _mitzaehlen("embed", genai_models.AsyncModels.embed_content)

# ---------- Dritte Korrektur: Deutsch ----------
# Die Anweisung "Fakten auf Deutsch" (custom_extraction_instructions) wirkt nur
# beim Extrahieren. Zusammenfassungen und Namen entstehen in eigenen Graphiti-
# Schritten mit englischen Prompts — gemessen am 12.09.2026: "Eike awaits the
# mayoral elections in Musterhausen". Deshalb bekommt jeder Aufruf den Hinweis mit.
_generieren = gemini_client.GeminiClient._generate_response

async def _auf_deutsch(self, messages, *args, **kwargs):
    return await _generieren(self, _deutsch_anhang(messages), *args, **kwargs)

gemini_client.GeminiClient._generate_response = _auf_deutsch

if __name__ == "__main__":
    server.main()
