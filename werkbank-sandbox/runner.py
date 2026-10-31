"""Läufer im Werkbank-Sandbox-Container: nimmt Aufträge als Datei an und ruft `opencode run` auf.

`/auftrag/<id>/in.json`  = {"prompt": str, "schema": dict|null, "modell": str}
`/auftrag/<id>/out.json` = {"ok": bool, "text": str, "json": dict|null, "fehler": str}

Warum ein eigener Container: `opencode run` führt Shell-Befehle aus, wenn der Text es verlangt (gemessen
05.10.2026, siehe docs/LEHREN.md). Hier gibt es darum weder `.env` noch `data/` noch Docker — nur den Ordner
`/auftrag`. Die JSON-Prüfung samt bis zu zwei Neuversuchen stammt aus dem früheren Host-Gateway.

Nur Standardbibliothek. Der Auftragsordner ist vom Friday-Container beschreibbar, also nicht vertrauenswürdig:
nur reguläre Dateien ohne Symlink, Größe begrenzt, Ordnernamen streng geprüft.
"""
import json
import os
import re
import stat
import subprocess
import sys
import time

AUFTRAG = os.environ.get("WERKBANK_AUFTRAG", "/auftrag")
ARBEITSORDNER = os.environ.get("WERKBANK_ARBEIT", "/projekt")   # nur die Projekt-Doku, schreibgeschützt (docker-compose.yml)
STANDARD_MODELL = "deepseek-v4.1-flash-free"
VERSUCHE = 3
TIMEOUT_S = 240
MAX_EINGABE = 1_000_000
ID_MUSTER = re.compile(r"^[0-9a-f]{16}$")
MODELL_MUSTER = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _prompt(prompt: str, schema: dict | None) -> str:
    if schema:
        prompt += ("\n\nAntworte NUR mit einem JSON-Objekt, das genau diesem JSON-Schema entspricht — "
                   "ohne Erklärung, ohne Codeblock. Jedes Pflichtfeld braucht einen echten Wert, niemals null; "
                   "Text-Pflichtfelder notfalls mit einem kurzen Satz füllen:\n" + json.dumps(schema, ensure_ascii=False))
    return prompt


def _opencode(modell: str, prompt: str) -> str:
    r = subprocess.run(["opencode", "run", "-m", f"opencode/{modell}", "--format", "json", "--dir", ARBEITSORDNER, prompt],
                       capture_output=True, text=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
    text = ""
    for zeile in r.stdout.splitlines():
        try:
            e = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("type") == "text":
            text += e.get("part", {}).get("text", "")
    if not text:
        raise RuntimeError("leere Antwort: " + (r.stdout[-200:] or r.stderr[-200:]))
    return text


def _json_aus(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("kein JSON in der Antwort")
    d = json.loads(m.group(0))
    if not isinstance(d, dict):
        raise ValueError("JSON ist kein Objekt")
    return d


def _pruefen(wert, schema: dict, wurzel: dict | None = None, pfad: str = "$") -> None:
    """Strenge, rekursive Prüfung gegen das JSON-Schema (Typ, Pflichtfelder, null, enum, $ref, anyOf)."""
    wurzel = wurzel or schema
    if "$ref" in schema:
        ziel = wurzel
        for teil in schema["$ref"].lstrip("#/").split("/"):
            ziel = ziel[teil]
        return _pruefen(wert, ziel, wurzel, pfad)
    alternativen = schema.get("anyOf", []) + schema.get("oneOf", [])
    if alternativen:
        for alt in alternativen:
            try:
                return _pruefen(wert, alt, wurzel, pfad)
            except ValueError:
                continue
        raise ValueError(f"{pfad}: passt zu keiner Alternative")
    typen = schema.get("type")
    typen = [typen] if isinstance(typen, str) else (typen or [])
    gueltig = {"string": str, "boolean": bool, "array": list, "object": dict, "null": type(None)}
    if typen:
        passt = any((t == "integer" and isinstance(wert, int) and not isinstance(wert, bool))
                    or (t == "number" and isinstance(wert, (int, float)) and not isinstance(wert, bool))
                    or (t in gueltig and isinstance(wert, gueltig[t])) for t in typen)
        if not passt:
            raise ValueError(f"{pfad}: Typ {typen} erwartet, bekam {type(wert).__name__}")
    if "enum" in schema and wert not in schema["enum"]:
        raise ValueError(f"{pfad}: Wert nicht erlaubt")
    if isinstance(wert, dict):
        fehlt = [k for k in schema.get("required", []) if k not in wert]
        if fehlt:
            raise ValueError(f"{pfad}: Pflichtfelder fehlen: {fehlt}")
        for k, v in schema.get("properties", {}).items():
            if k in wert:
                _pruefen(wert[k], v, wurzel, f"{pfad}.{k}")
    if isinstance(wert, list) and "items" in schema:
        for i, x in enumerate(wert):
            _pruefen(x, schema["items"], wurzel, f"{pfad}[{i}]")


def beantworten(prompt: str, schema: dict | None, modell: str) -> dict:
    """Ergebnis-Dict für out.json. Ohne Schema: Freitext in `text`. Mit Schema: geprüftes JSON, bis zu 3 Versuche."""
    modell = (modell or STANDARD_MODELL).removeprefix("opencode/")
    if not MODELL_MUSTER.match(modell):
        return {"ok": False, "text": "", "json": None, "fehler": "Modellname ungültig"}
    voll = _prompt(prompt, schema)
    letzter = None
    for versuch in range(VERSUCHE):
        if versuch:
            time.sleep(2)
        try:
            text = _opencode(modell, voll)
            if not schema:
                return {"ok": True, "text": text, "json": None, "fehler": ""}
            d = _json_aus(text)
            _pruefen(d, schema)
            return {"ok": True, "text": text, "json": d, "fehler": ""}
        except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
            letzter = e
    return {"ok": False, "text": "", "json": None,
            "fehler": f"nach {VERSUCHE} Versuchen ohne brauchbare Antwort: {letzter}"[:400]}


def _lesen(ordner_fd: int, name: str) -> bytes | None:
    """Nur eine echte Datei (kein Symlink, kein FIFO), höchstens MAX_EINGABE Bytes."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=ordner_fd)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_EINGABE:
            return None
        return f.read(MAX_EINGABE + 1)


def _schreiben(ordner_fd: int, ergebnis: dict) -> None:
    tmp = "out.tmp"
    try:
        os.unlink(tmp, dir_fd=ordner_fd)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=ordner_fd)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(ergebnis, f, ensure_ascii=False)
    os.replace(tmp, "out.json", src_dir_fd=ordner_fd, dst_dir_fd=ordner_fd)


def auftrag_bearbeiten(auftrag: str, kennung: str) -> bool:
    """Einen Auftrag ausführen. True, wenn es einen (neuen) gab."""
    if not ID_MUSTER.match(kennung):
        return False
    try:
        wurzel = os.open(auftrag, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return False
    try:
        try:
            fd = os.open(kennung, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=wurzel)
        except OSError:
            return False
        try:
            if _lesen(fd, "out.json") is not None:
                return False                                   # schon erledigt
            roh = _lesen(fd, "in.json")
            if roh is None:
                return False
            try:
                eingabe = json.loads(roh.decode("utf-8"))
                prompt = eingabe["prompt"]
                schema = eingabe.get("schema")
                if not isinstance(prompt, str) or not (schema is None or isinstance(schema, dict)):
                    raise ValueError("Eingabe hat falsche Typen")
                modell = eingabe.get("modell") or ""
                if not isinstance(modell, str):
                    raise ValueError("Modell ist kein Text")
                ergebnis = beantworten(prompt, schema, modell)
            except (ValueError, KeyError, AttributeError) as e:
                ergebnis = {"ok": False, "text": "", "json": None, "fehler": f"Eingabe unbrauchbar: {e}"[:300]}
            _schreiben(fd, ergebnis)
            return True
        finally:
            os.close(fd)
    finally:
        os.close(wurzel)


def schleife() -> None:
    os.makedirs(ARBEITSORDNER, exist_ok=True)
    print("Werkbank-Sandbox bereit", flush=True)
    while True:
        try:
            namen = sorted(os.listdir(AUFTRAG))
        except OSError:
            namen = []
        gearbeitet = False
        for n in namen:
            try:
                gearbeitet = auftrag_bearbeiten(AUFTRAG, n) or gearbeitet
            except OSError as e:
                print("Auftrag", n, "Fehler:", e, file=sys.stderr, flush=True)
        if not gearbeitet:
            time.sleep(1)


if __name__ == "__main__":
    schleife()
