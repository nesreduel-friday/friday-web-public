"""Web Push — die Grundlage für Benachrichtigungen aufs Handy.

Der Browser meldet sich beim Push-Dienst seines Herstellers an (bei Apple
`web.push.apple.com`) und gibt Friday eine Adresse plus zwei Schlüssel. Damit
kann der Server eine verschlüsselte Nachricht schicken, auch wenn die App
zu ist. Verschlüsselt wird mit `pywebpush`, nicht selbst gebaut.

Wichtig fürs iPhone: Web Push geht dort NUR, wenn die Seite über
„Zum Home-Bildschirm" installiert ist, und die Erlaubnis muss aus einem
echten Fingertipp kommen. Beides passiert im Browser (`web/push.js`).

Zwei Dateien im Datenordner, beide nur für Friday lesbar (600):
- `push_schluessel.pem` — der private VAPID-Schlüssel, einmal erzeugt
- `push_geraete.json` — die angemeldeten Geräte

Ein Gerät, das der Push-Dienst mit 404 oder 410 ablehnt, ist endgültig weg
(App gelöscht, Erlaubnis entzogen) und wird ausgetragen.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from pathlib import Path

from kern import config

log = logging.getLogger("friday.push")

# VAPID verlangt eine Kontaktangabe. Sie MUSS eine mailto:-Adresse mit gültiger
# Domain sein: mit `mailto:friday@hphome.local` antwortete Apple auf jeden Versuch
# mit 403 {"reason":"BadJwtToken"} (gemessen 13.09.2026) — die Benachrichtigung kam
# nie an. Mit einer echten Domain wird sie angenommen. `py_vapid` lässt ausserdem
# nur mailto: zu und weist https:-Angaben selbst ab.
KONTAKT = os.getenv("PUSH_KONTAKT", "").strip() or "mailto:friday@hphome.beispiel.ts.net"
ZEITLIMIT = 15
_sperre = threading.Lock()


def _pfad(name: str) -> Path:
    return config.daten_ordner() / name


def _vapid():
    """Den VAPID-Schlüssel laden — beim ersten Mal erzeugen und ablegen."""
    from py_vapid import Vapid01

    datei = _pfad("push_schluessel.pem")
    if datei.exists():
        return Vapid01.from_file(str(datei))
    datei.parent.mkdir(parents=True, exist_ok=True)
    v = Vapid01()
    v.generate_keys()
    v.save_key(str(datei))
    os.chmod(datei, 0o600)
    log.info("VAPID-Schlüssel erzeugt")
    return v


def oeffentlicher_schluessel() -> str:
    """Der öffentliche Schlüssel für den Browser (base64url, ohne Polster)."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    roh = _vapid().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return urlsafe_b64encode(roh).rstrip(b"=").decode()


def _geraete_lesen() -> list[dict]:
    try:
        daten = json.loads(_pfad("push_geraete.json").read_text(encoding="utf-8"))
        return daten if isinstance(daten, list) else []
    except (OSError, ValueError):
        return []


def _geraete_schreiben(liste: list[dict]) -> None:
    ziel = _pfad("push_geraete.json")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False, indent=1)
    os.replace(neu, ziel)


def anmelden(anmeldung: dict, geraet: str = "") -> int:
    """Ein Gerät eintragen. Dieselbe Adresse ersetzt den alten Eintrag."""
    adresse = (anmeldung or {}).get("endpoint", "")
    if not adresse or not (anmeldung.get("keys") or {}).get("auth"):
        raise ValueError("unvollständige Anmeldung")
    with _sperre:
        liste = [g for g in _geraete_lesen() if g.get("endpoint") != adresse]
        liste.append({"endpoint": adresse, "keys": anmeldung["keys"], "geraet": (geraet or "")[:80],
                      "seit": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        _geraete_schreiben(liste)
        return len(liste)


def abmelden(adresse: str) -> int:
    with _sperre:
        liste = _geraete_lesen()
        bleibt = [g for g in liste if g.get("endpoint") != adresse]
        _geraete_schreiben(bleibt)
        return len(liste) - len(bleibt)


def geraete() -> list[dict]:
    """Nur die harmlosen Angaben — nie die Schlüssel."""
    return [{"geraet": g.get("geraet", ""), "seit": g.get("seit", ""),
             "dienst": (g.get("endpoint", "").split("/")[2] if "/" in g.get("endpoint", "") else "")}
            for g in _geraete_lesen()]


def senden(titel: str, text: str, url: str = "/", kennung: str = "") -> dict:
    """An alle angemeldeten Geräte. Gibt zurück, wie viele es bekommen haben."""
    from pywebpush import WebPushException, webpush

    liste = _geraete_lesen()
    if not liste:
        return {"gesendet": 0, "geraete": 0, "abgemeldet": 0}
    inhalt = json.dumps({"titel": titel, "text": text, "url": url, "tag": kennung}, ensure_ascii=False)
    datei = str(_pfad("push_schluessel.pem"))
    _vapid()   # sicherstellen, dass der Schlüssel existiert
    gesendet, weg, grund = 0, [], ""
    for g in liste:
        try:
            webpush(subscription_info={"endpoint": g["endpoint"], "keys": g["keys"]}, data=inhalt,
                    vapid_private_key=datei, vapid_claims={"sub": KONTAKT}, timeout=ZEITLIMIT)
            gesendet += 1
        except WebPushException as exc:
            antwort = getattr(exc, "response", None)
            code = getattr(antwort, "status_code", 0)
            # Den Grund merken und melden — ihn nur zu verschlucken hat mich am
            # 13.09.2026 lange raten lassen, warum nichts ankam.
            # str() um den Text herum: was die Bibliothek dort ablegt, ist nicht
            # garantiert eine Zeichenkette — ein Fehler HIER würde die ganze
            # Sendeschleife abbrechen, mitten in der Fehlerbehandlung.
            grund = f"{code}: {str(getattr(antwort, 'text', '') or exc)[:160]}"
            if code in (404, 410):
                weg.append(g["endpoint"])          # Gerät gibt es nicht mehr
                log.info("Gerät ausgetragen (%s)", code)
            else:
                log.warning("Push fehlgeschlagen — %s", grund)
        except Exception as exc:
            grund = f"{type(exc).__name__}: {str(exc)[:160]}"
            log.warning("Push fehlgeschlagen — %s", grund)
    if weg:
        with _sperre:
            _geraete_schreiben([g for g in _geraete_lesen() if g.get("endpoint") not in weg])
    return {"gesendet": gesendet, "geraete": len(liste), "abgemeldet": len(weg), "grund": grund}
