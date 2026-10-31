"""Eine gemeinsame Sperre für Hintergrundaufträge mit Modell-Schleife (15.10.2026): Recherche und Aufgabenblätter laufen nie gleichzeitig,
damit das Gemini-Kontingent nicht doppelt belastet wird."""

import asyncio

SPERRE = asyncio.Lock()
