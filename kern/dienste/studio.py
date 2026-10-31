"""Studio — der YouTube-Kanal (Beispielkanal).

Phase 3: Kanalzahlen lesen, Leistung der letzten Videos auswerten, Videos
veröffentlichen. Das Planen und Rendern macht der eigene Studio-Container
(siehe studio/arbeiter.py); hier steht nur, was mit YouTube spricht.

Zugang: OAuth wie bei Spotify. Eike trägt Client ID und Secret eines
**Web**-Clients auf der Freigaben-Seite ein und tippt auf „Mit YouTube
verbinden". Der Desktop-Client, den Jarvis benutzte, taugt dafür nicht: Er
kennt nur `http://localhost`, und auf dem Server läuft kein Browser. Der
Geräte-Flow für Fernseher scheidet ebenfalls aus — an Eikes Client gemessen
(14.09.2026) antwortet Google dort mit `invalid_client: Invalid client type`.

Ohne OAuth geht gar nichts, auch kein bloßes Nachsehen: Die YouTube Data API
weist einfache API-Schlüssel ab („API keys are not supported by this API“,
ebenfalls gemessen).
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from kern import config, konnektoren

KONNEKTOR = "studio"
KANAL = "Beispielkanal"

ANMELDUNG = "https://accounts.google.com/o/oauth2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
ZEITLIMIT = 20.0

# Unter so vielen Aufrufen sagt die Rückhaltung nichts: Ein Video, das nur
# zwanzig Menschen erreicht hat, kann 74 % halten und trotzdem kein Vorbild sein.
MINDESTAUFRUFE = 200

# Lesen, hochladen, auswerten. Dieselben vier wie bei Jarvis — mehr braucht es
# nicht, und weniger reicht nicht: ohne yt-analytics keine Rückhaltedaten, und
# ohne die plant Friday blind.
RECHTE = " ".join([
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
])

_staat = ""


class StudioFehler(Exception):
    pass


class ZugangFehler(StudioFehler):
    """Der Zugang selbst ist das Problem (nie verbunden, zurückgezogen) — nicht ein einzelner Bericht.

    Die Statistik zeigt bei einem einzelnen fehlgeschlagenen Bericht nur diesen
    Abschnitt als nicht verfügbar. Bei diesem Fehler hilft nur neu verbinden.
    """


# ---------- Zugang ----------

def zugang() -> tuple[str, str]:
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise StudioFehler("Für das Studio sind noch keine Client ID und kein Client Secret eingetragen.")
    return z


def rueckadresse() -> str:
    """Muss in der Google Cloud Console EXAKT so als Rückadresse eingetragen sein."""
    return config.basis_url().rstrip("/") + "/api/studio/rueck"


def _tokendatei():
    return config.daten_ordner() / "studio_token.json"


def _token_lesen() -> dict:
    try:
        return json.loads(_tokendatei().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _token_schreiben(daten: dict) -> None:
    ziel = _tokendatei()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(daten, f)
    os.replace(neu, ziel)


def verbunden() -> bool:
    return bool(_token_lesen().get("refresh_token"))


def trennen() -> None:
    try:
        _tokendatei().unlink()
    except OSError:
        pass


def anmelde_url() -> str:
    """Wohin Eike geschickt wird, um den Zugriff zu erlauben."""
    global _staat
    kennung, _ = zugang()
    _staat = secrets.token_urlsafe(16)
    return ANMELDUNG + "?" + urlencode({
        "client_id": kennung, "response_type": "code", "redirect_uri": rueckadresse(),
        "scope": RECHTE, "state": _staat,
        # Ohne beides gibt Google beim zweiten Mal KEINEN refresh_token mehr
        # heraus, und Friday stünde nach einer Stunde ohne Zugang da.
        "access_type": "offline", "prompt": "consent"})


async def code_einloesen(code: str, staat: str) -> None:
    """Den Rückläufer von Google in einen dauerhaften Zugang tauschen."""
    if not _staat or staat != _staat:
        raise StudioFehler("Die Antwort von Google passt nicht zur Anfrage. Bitte neu verbinden.")
    kennung, geheim = zugang()
    async with httpx.AsyncClient(timeout=ZEITLIMIT) as client:
        antwort = await client.post(TOKEN, data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": rueckadresse(), "client_id": kennung, "client_secret": geheim})
    if antwort.status_code != 200:
        raise StudioFehler(f"Google gibt keinen Zugang heraus ({antwort.status_code}: {antwort.text[:150]}).")
    d = antwort.json()
    if not d.get("refresh_token"):
        raise StudioFehler("Google hat keinen dauerhaften Zugang mitgeschickt. Bitte noch einmal verbinden.")
    _token_schreiben({"refresh_token": d["refresh_token"], "access_token": d.get("access_token", ""),
                      "gilt_bis": (datetime.now(timezone.utc)
                                   + timedelta(seconds=int(d.get("expires_in", 3600)) - 60)).isoformat()})


async def _zugriffstoken() -> str:
    daten = _token_lesen()
    if not daten.get("refresh_token"):
        raise ZugangFehler("Das Studio ist noch nicht mit YouTube verbunden.")
    try:
        gilt_bis = datetime.fromisoformat(str(daten.get("gilt_bis")))
    except (TypeError, ValueError):
        gilt_bis = datetime.now(timezone.utc)
    if daten.get("access_token") and gilt_bis > datetime.now(timezone.utc):
        return daten["access_token"]

    kennung, geheim = zugang()
    async with httpx.AsyncClient(timeout=ZEITLIMIT) as client:
        antwort = await client.post(TOKEN, data={
            "grant_type": "refresh_token", "refresh_token": daten["refresh_token"],
            "client_id": kennung, "client_secret": geheim})
    if antwort.status_code != 200:
        raise ZugangFehler("Der YouTube-Zugang wurde zurückgezogen. Bitte neu verbinden.")
    d = antwort.json()
    daten["access_token"] = d.get("access_token", "")
    daten["gilt_bis"] = (datetime.now(timezone.utc)
                         + timedelta(seconds=int(d.get("expires_in", 3600)) - 60)).isoformat()
    _token_schreiben(daten)
    return daten["access_token"]


async def _abfrage(url: str, params: dict) -> dict:
    token = await _zugriffstoken()
    async with httpx.AsyncClient(timeout=ZEITLIMIT) as client:
        antwort = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    if antwort.status_code == 401:
        raise ZugangFehler("Der YouTube-Zugang wurde zurückgezogen. Bitte neu verbinden.")
    if antwort.status_code == 403:
        raise StudioFehler(f"YouTube lässt das nicht zu: {antwort.text[:160]}")
    if antwort.status_code != 200:
        raise StudioFehler(f"YouTube antwortet mit {antwort.status_code}: {antwort.text[:120]}")
    return antwort.json()


# ---------- Lesen ----------

async def kanal() -> dict:
    """Die Eckdaten des Kanals — Aufrufe, Abonnenten, Anzahl Videos."""
    d = await _abfrage(f"{API}/channels", {"part": "snippet,statistics,contentDetails", "mine": "true"})
    eintraege = d.get("items") or []
    if not eintraege:
        raise StudioFehler("Zu diesem Zugang gehört kein Kanal.")
    k = eintraege[0]
    s, st = k.get("snippet") or {}, k.get("statistics") or {}
    hochgeladen = ((k.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads", "")
    return {"id": k.get("id", ""), "name": s.get("title", ""), "uploads": hochgeladen,
            "seit": str(s.get("publishedAt", ""))[:10],
            "aufrufe": int(st.get("viewCount") or 0),
            "abonnenten": int(st.get("subscriberCount") or 0),
            "videos": int(st.get("videoCount") or 0)}


async def leistung(tage: int = 120) -> dict:
    """Wie die bisherigen Videos liefen — die Grundlage für die Themenwahl.

    `sort` ist bei dieser Abfrage PFLICHT: Ohne Sortierfeld antwortet die
    Analytics-Schnittstelle mit „The query is not supported" (gemessen
    14.09.2026, zwei Fehlversuche). Mit `sort=-views` kommen alle Videos in
    EINEM Aufruf statt in 35 Einzelabfragen.

    Entscheidend ist `averageViewPercentage`: Sie bestimmt, ob YouTube ein Video
    überhaupt weiter ausspielt. Abonnenten sind eine zweite, davon unabhängige
    Größe — auf Eikes Kanal brachte das Video mit der DRITTschlechtesten
    Rückhaltung die meisten Abos.
    """
    heute = datetime.now(timezone.utc).date()
    d = await _abfrage(ANALYTICS, {
        "ids": "channel==MINE",
        "startDate": (heute - timedelta(days=max(7, tage))).isoformat(),
        "endDate": heute.isoformat(),
        "metrics": "views,averageViewPercentage,averageViewDuration,subscribersGained",
        "dimensions": "video", "sort": "-views", "maxResults": 50})
    zeilen = d.get("rows") or []
    if not zeilen:
        return {"videos": [], "zusammenfassung": ""}

    namen = {}
    kennungen = [z[0] for z in zeilen]
    for i in range(0, len(kennungen), 50):
        v = await _abfrage(f"{API}/videos", {"part": "snippet", "id": ",".join(kennungen[i:i + 50])})
        for e in v.get("items") or []:
            namen[e["id"]] = (e.get("snippet") or {}).get("title", "")

    videos = [{"id": z[0], "titel": namen.get(z[0], z[0]), "aufrufe": int(z[1] or 0),
               "rueckhaltung": float(z[2] or 0), "sekunden": float(z[3] or 0),
               "abos": int(z[4] or 0)} for z in zeilen]
    videos.sort(key=lambda v: v["rueckhaltung"], reverse=True)

    k = await kanal()
    je_tausend = (k["abonnenten"] / k["aufrufe"] * 1000) if k["aufrufe"] else 0
    zusammenfassung = (f"{k['name']}: {k['aufrufe']} Aufrufe, {k['abonnenten']} Abonnenten, "
                       f"{k['videos']} Videos — {je_tausend:.1f} Abonnenten je 1000 Aufrufe.")
    return {"videos": videos, "zusammenfassung": zusammenfassung, "kanal": k}


def leistungstext(daten: dict) -> str:
    """Die Zahlen als Text, den ein Planer verwerten kann.

    Absichtlich nach Ursache getrennt: Was gut gehalten wurde, wurde weiter
    ausgespielt; was abfiel, nicht. Und Abonnenten stehen für sich, weil sie auf
    diesem Kanal gerade die knappste Größe sind.
    """
    videos = daten.get("videos") or []
    if not videos:
        return daten.get("zusammenfassung", "")
    zeilen = [daten.get("zusammenfassung", "")]

    # Nur Videos, die überhaupt ausgespielt wurden, taugen als Vorbild. „Why
    # Beavers Hate the Sound of Running Water" hält 74 % — bei 20 Aufrufen. Aus
    # solchen Zahlen zu lernen hieße, Rauschen für ein Signal zu halten
    # (gemessen 14.09.2026).
    belastbar = [v for v in videos if v["aufrufe"] >= MINDESTAUFRUFE]
    gut = [v for v in belastbar if v["rueckhaltung"] >= 55][:8]
    schwach = [v for v in sorted(belastbar, key=lambda v: v["rueckhaltung"])
               if v["rueckhaltung"] < 45][:8]
    abos = [v for v in sorted(videos, key=lambda v: v["abos"], reverse=True) if v["abos"] > 0][:5]

    if gut:
        zeilen += ["", "GUT GEHALTEN (wurden deshalb weiter ausgespielt) — davon mehr:"]
        zeilen += [f"- {v['titel']!r}: {v['rueckhaltung']:.0f}% geschaut, {v['aufrufe']} Aufrufe" for v in gut]
    if schwach:
        zeilen += ["", "ABGEBROCHEN (unter 45% — YouTube hörte auf, sie zu zeigen) — so etwas meiden:"]
        zeilen += [f"- {v['titel']!r}: nur {v['rueckhaltung']:.0f}% geschaut" for v in schwach]
    if abos:
        zeilen += ["", "BRACHTEN ABONNENTEN (das Knappste überhaupt):"]
        zeilen += [f"- {v['titel']!r}: +{v['abos']} bei {v['aufrufe']} Aufrufen" for v in abos]
    return "\n".join(zeilen)


def _pruefen(datei) -> list[str]:
    """Was gegen eine Veröffentlichung spricht. Leere Liste heißt: in Ordnung.

    Der Kanal ist öffentlich — ein abgebrochenes Rendering darf dort nicht
    landen. Fehlt ffprobe, wird NICHT blockiert: ein Fehlalarm wäre schlimmer
    als eine ungeprüfte Datei (Gedanke aus Jarvis' Fassung übernommen).
    """
    import json as _json
    import subprocess
    from pathlib import Path

    pfad = Path(datei)
    if not pfad.exists():
        return ["die Datei gibt es nicht"]
    if pfad.stat().st_size == 0:
        return ["die Datei ist leer"]
    try:
        lauf = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                               "-show_format", "-show_streams", str(pfad)],
                              capture_output=True, text=True, timeout=60)
        daten = _json.loads(lauf.stdout or "{}") if lauf.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        daten = None
    if not daten:
        return []

    stroeme = daten.get("streams") or []
    bild = next((s for s in stroeme if s.get("codec_type") == "video"), None)
    ton = any(s.get("codec_type") == "audio" for s in stroeme)
    try:
        dauer = float((daten.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dauer = 0.0

    probleme = []
    if not bild:
        probleme.append("es ist kein Bild darin")
    else:
        breite, hoehe = int(bild.get("width") or 0), int(bild.get("height") or 0)
        if not breite or not hoehe:
            probleme.append("es ist kein Bild darin")
        elif hoehe <= breite:
            probleme.append(f"es ist nicht hochkant ({breite}×{hoehe}) — YouTube nimmt es dann nicht als Short")
    if not ton:
        probleme.append("es hat keine Tonspur")
    if dauer and dauer > 180:
        probleme.append(f"es ist {dauer:.0f} Sekunden lang — zu lang für einen Short")
    elif dauer and dauer < 5:
        probleme.append(f"es ist nur {dauer:.1f} Sekunden lang — das Rendern brach wohl ab")
    return probleme


async def hochladen(datei, titel: str, beschreibung: str, schlagworte: list[str],
                    oeffentlich: bool = True) -> dict:
    """Ein fertiges Video veröffentlichen. Nur nach Eikes ausdrücklichem Ja.

    `containsSyntheticMedia` ist Pflicht und kein Beiwerk: Skript und Stimme
    stammen aus KI. Das Feld steht so in der API-Dokumentation von Google
    (nachgelesen 14.09.2026); Jarvis' Fassung setzte es nicht. Zusätzlich steht
    der Hinweis im Beschreibungstext — das eine ist YouTubes Kennzeichnung,
    das andere liest ein Mensch.
    """
    from pathlib import Path

    pfad = Path(datei)
    probleme = _pruefen(pfad)
    if probleme:
        raise StudioFehler("Ich lade das nicht hoch, weil " + " und ".join(probleme) + ".")

    if "KI" not in beschreibung and "AI" not in beschreibung:
        beschreibung = (beschreibung.rstrip() + "\n\n"
                        "Script and narration for this video were created with the help of AI. "
                        "All footage is real stock material, not AI-generated.")

    koerper = {
        "snippet": {"title": titel[:100], "description": beschreibung[:5000],
                    "tags": [str(t)[:100] for t in schlagworte][:30], "categoryId": "28"},
        "status": {"privacyStatus": "public" if oeffentlich else "private",
                   "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": True},
    }

    token = await _zugriffstoken()
    # Wiederaufnehmbarer Upload: erst die Angaben, dann die Datei in einem Stück.
    async with httpx.AsyncClient(timeout=600.0) as client:
        start = await client.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"part": "snippet,status", "uploadType": "resumable"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=koerper)
        if start.status_code not in (200, 201):
            raise StudioFehler(f"YouTube nimmt das Video nicht an ({start.status_code}: {start.text[:200]}).")
        ziel = start.headers.get("location")
        if not ziel:
            raise StudioFehler("YouTube hat keine Adresse zum Hochladen mitgeschickt.")

        inhalt = pfad.read_bytes()
        antwort = await client.put(ziel, content=inhalt, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
            "Content-Length": str(len(inhalt))})
    if antwort.status_code not in (200, 201):
        raise StudioFehler(f"Das Hochladen ist fehlgeschlagen ({antwort.status_code}: {antwort.text[:200]}).")

    d = antwort.json()
    kennung = d.get("id", "")
    return {"id": kennung, "url": f"https://youtu.be/{kennung}" if kennung else "",
            "titel": titel, "oeffentlich": oeffentlich}


async def letzte_videos(anzahl: int = 10) -> list[dict]:
    """Die zuletzt veröffentlichten Videos mit ihren Aufrufen."""
    k = await kanal()
    if not k["uploads"]:
        return []
    liste = await _abfrage(f"{API}/playlistItems", {
        "part": "snippet,contentDetails", "playlistId": k["uploads"], "maxResults": max(1, min(anzahl, 50))})
    kennungen = [(e.get("contentDetails") or {}).get("videoId", "") for e in liste.get("items") or []]
    kennungen = [x for x in kennungen if x]
    if not kennungen:
        return []
    zahlen = await _abfrage(f"{API}/videos", {"part": "snippet,statistics,contentDetails",
                                              "id": ",".join(kennungen)})
    videos = []
    for v in zahlen.get("items") or []:
        st, sn = v.get("statistics") or {}, v.get("snippet") or {}
        videos.append({"id": v.get("id", ""), "titel": sn.get("title", ""),
                       "veroeffentlicht": str(sn.get("publishedAt", ""))[:10],
                       "aufrufe": int(st.get("viewCount") or 0),
                       "likes": int(st.get("likeCount") or 0),
                       "kommentare": int(st.get("commentCount") or 0)})
    return videos
