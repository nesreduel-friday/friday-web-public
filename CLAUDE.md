# friday-web

@AGENTS.md

## Zusätzlich für Claude Code
- **Navigation:** `docs/KARTE.md` ordnet jede Funktion ihren Dateien zu (Kern, Werkzeug, Route, Web, Test).
  Erst dort nachsehen, dann die Dateien direkt öffnen, nicht das Projekt durchsuchen.
- **Suchen:** Pfad bekannt → direkt öffnen. Stichwort → `qmd search "…"` (Volltext, Datei:Zeile, sofort).
  Nur bei unklarer Formulierung → `qmd query --no-rerank "…"` (semantisch, ~15 s).
  **Nie `qmd query` ohne `--no-rerank`:** Der Reranker rechnet hier auf der CPU minutenlang.
  Nach größeren Änderungen: `qmd update && qmd embed`.
- **Lange Dateien nur gezielt lesen:** `kern/main.py` (~2600 Zeilen), `docs/LEHREN.md` (~2200 Zeilen).
  Erst `grep -n`, dann den Zeilenbereich.
- **Geheimnisse:** `.env` und die Token-/Zugangsdateien in `data/` nie ausgeben. Schlüssel nur auf „vorhanden/fehlt“ prüfen.
- **Gezielt testen** (ein Modul): `docker compose run --rm --build -e FRIDAY_HINTERGRUND=aus friday python -m unittest test.test_<name> -v`.
  Vor jedem Commit die ganze Suite (Befehl unter „Betrieb“).
- **Live bringen und prüfen:** `docker compose up -d --build friday`, dann `curl -s http://127.0.0.1:8124/api/health`
  und die Logs (`docker compose logs --tail 50 friday`).
- **Oberfläche testen:** Playwright-MCP nur gegen `http://127.0.0.1:8124`, nur lesen und klicken, nichts absenden,
  was nach außen geht (Garantie-Anträge, WhatsApp, Push).
