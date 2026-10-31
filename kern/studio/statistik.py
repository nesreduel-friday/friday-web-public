"""Statistiken des Kanals und seiner Videos — für den Studio-Tab.

Nur LESEN, alles über die YouTube Analytics API und die Data API mit dem
vorhandenen OAuth-Zugang (`kern/dienste/studio.py`). Nichts davon geht in ein
Werkzeug oder ins Gedächtnis; die Zahlen liegen kurz im Arbeitsspeicher.

Grundsätze:
- Ein Bericht, der fehlschlägt (YouTube lehnt manche Kombinationen ab, kleine
  Kanäle haben für Alter/Geschlecht oft gar keine Daten), lässt NUR seinen
  Abschnitt ausfallen. Nur ein kaputter Zugang (`ZugangFehler`) bricht alles ab.
- Nicht verfügbar ist etwas anderes als null: Abschnitte tragen `ok` und
  `fehler`, die Seite zeigt den Unterschied.
- Kein Bericht gibt es für Impressionen und Klickrate — die liefert YouTube
  nicht über die API, nur im Studio selbst.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

from kern.dienste import studio
from kern.dienste.studio import StudioFehler, ZugangFehler

TAGE_ERLAUBT = (7, 28, 90, 365, 0)          # 0 = seit Kanalstart
FRISCH_S = 600                              # so lange gelten Zahlen aus dem Zwischenspeicher
MAX_VIDEOS = 200                            # Obergrenze des Video-Berichts der API
SHORT_MAX_S = 180                           # bis dahin zählt ein Video als Short

_speicher: dict[tuple, tuple[float, object]] = {}

# Metriken der Tagesreihe und der Summe. `averageViewPercentage` heißt bei
# YouTube „durchschnittlich angesehen“ und ist die Größe, die über die weitere
# Ausspielung entscheidet.
SUMME = ("views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
         "subscribersGained,subscribersLost,likes,comments,shares")
VIDEO_VOLL = ("views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
              "subscribersGained,likes,comments,shares")
# Der Satz, der am 14.09.2026 nachweislich funktionierte (studio.leistung) —
# Rückfall, falls YouTube den großen Satz für Videos ablehnt.
VIDEO_KERN = "views,averageViewPercentage,averageViewDuration,subscribersGained"

QUELLEN = {
    "YT_SEARCH": "YouTube-Suche", "SHORTS": "Shorts-Feed", "RELATED_VIDEO": "Vorgeschlagene Videos",
    "SUBSCRIBER": "Abo-Feed", "YT_CHANNEL": "Kanalseiten", "NOTIFICATION": "Benachrichtigungen",
    "EXT_URL": "Externe Webseiten", "NO_LINK_OTHER": "Direkt / unbekannt",
    "NO_LINK_EMBEDDED": "Eingebettet ohne Link", "PLAYLIST": "Playlists",
    "YT_OTHER_PAGE": "Andere YouTube-Seiten", "YT_PLAYLIST_PAGE": "Playlist-Seiten",
    "END_SCREEN": "Endbildschirm", "HASHTAGS": "Hashtag-Seiten", "SOUND_PAGE": "Sound-Seiten",
    "ADVERTISING": "Werbung", "PROMOTED": "Beworben", "VIDEO_REMIXES": "Remixe",
    "CAMPAIGN_CARD": "Kampagnenkarten", "ANNOTATION": "Anmerkungen", "LIVE_REDIRECT": "Live-Weiterleitung",
    "PRODUCT_PAGE": "Produktseiten", "SHOPPING": "Shopping",
}
GERAETE = {"MOBILE": "Handy", "DESKTOP": "Computer", "TABLET": "Tablet", "TV": "Fernseher",
           "GAME_CONSOLE": "Spielkonsole", "UNKNOWN_PLATFORM": "Unbekannt"}
ORTE = {"WATCH": "YouTube-Wiedergabeseite", "CHANNEL": "Kanalseite", "EMBEDDED": "Eingebettet",
        "EXTERNAL_APP": "Externe App", "MOBILE": "Handy-App", "SEARCH": "Suchergebnisse",
        "YT_OTHER": "Sonstiges bei YouTube", "BROWSE": "Startseite / Entdecken"}
INHALT = {"SHORTS": "Shorts", "VIDEO_ON_DEMAND": "Lange Videos", "LIVE_STREAM": "Livestreams",
          "STORY": "Stories"}
ABO = {"SUBSCRIBED": "Abonnenten", "UNSUBSCRIBED": "Nicht abonniert"}
GESCHLECHT = {"female": "weiblich", "male": "männlich", "user_specified": "divers"}
BETRIEBSSYSTEM = {"ANDROID": "Android", "IOS": "iOS", "WINDOWS": "Windows", "MACINTOSH": "macOS",
                  "LINUX": "Linux", "CHROMECAST": "Chromecast", "WEB_OS": "webOS", "TIZEN": "Tizen",
                  "FIRE_OS": "Fire OS", "PLAYSTATION": "PlayStation", "XBOX": "Xbox",
                  "SMART_TV": "Smart-TV", "TVOS": "tvOS", "ROKU": "Roku"}
TEILEN = {"WHATS_APP": "WhatsApp", "COPY_PASTE": "Link kopiert", "FACEBOOK": "Facebook",
          "TWITTER": "X / Twitter", "MESSAGES": "Nachrichten", "GMAIL": "Gmail",
          "TELEGRAM": "Telegram", "EMAIL": "E-Mail", "INSTAGRAM_STORIES": "Instagram",
          "SNAPCHAT": "Snapchat", "REDDIT": "Reddit", "SMS": "SMS", "KAKAO_TALK": "KakaoTalk",
          "LINKEDIN": "LinkedIn", "PINTEREST": "Pinterest", "OTHER": "Sonstiges",
          "UNKNOWN_OTHER": "Sonstiges", "SYSTEM_SHARE_SHEET": "Teilen-Menü"}


def _schoen(tabelle: dict, roh: str) -> str:
    return tabelle.get(roh) or str(roh).replace("_", " ").capitalize()


# ---------- Zwischenspeicher und Berichte ----------

def leeren() -> None:
    _speicher.clear()


async def _bericht(params: dict, frisch: bool = False) -> list[dict]:
    """Ein Analytics-Bericht als Liste von Zeilen {Spaltenname: Wert}."""
    schluessel = ("a", tuple(sorted((k, str(v)) for k, v in params.items())))
    if not frisch and (t := _speicher.get(schluessel)) and time.monotonic() - t[0] < FRISCH_S:
        return t[1]
    d = await studio._abfrage(studio.ANALYTICS, {"ids": "channel==MINE", **params})
    namen = [c.get("name", "") for c in d.get("columnHeaders") or []]
    zeilen = [dict(zip(namen, z)) for z in d.get("rows") or []]
    if len(_speicher) > 400:
        _speicher.clear()
    _speicher[schluessel] = (time.monotonic(), zeilen)
    return zeilen


async def _daten(pfad: str, params: dict, frisch: bool = False) -> dict:
    """Ein Aufruf der Data API mit demselben Zwischenspeicher."""
    schluessel = ("d", pfad, tuple(sorted((k, str(v)) for k, v in params.items())))
    if not frisch and (t := _speicher.get(schluessel)) and time.monotonic() - t[0] < FRISCH_S:
        return t[1]
    d = await studio._abfrage(f"{studio.API}/{pfad}", params)
    _speicher[schluessel] = (time.monotonic(), d)
    return d


async def _kanal(frisch: bool = False) -> dict:
    schluessel = ("k",)
    if not frisch and (t := _speicher.get(schluessel)) and time.monotonic() - t[0] < FRISCH_S:
        return t[1]
    k = await studio.kanal()
    _speicher[schluessel] = (time.monotonic(), k)
    return k


async def _abschnitt(coro) -> dict:
    """Ein Teilbericht: schlägt er fehl, fehlt nur dieser Abschnitt."""
    try:
        zeilen = await coro
    except ZugangFehler:
        raise
    except StudioFehler as exc:
        return {"ok": False, "fehler": str(exc), "zeilen": []}
    except Exception as exc:                               # Netz, Zeitlimit …
        return {"ok": False, "fehler": f"nicht erreichbar ({type(exc).__name__})", "zeilen": []}
    return {"ok": True, "fehler": "", "zeilen": zeilen}


# ---------- Zeiträume ----------

def zeitraum(tage: int, seit: str = "") -> dict:
    """Von/bis und der gleich lange Zeitraum davor (für die Veränderung)."""
    if tage not in TAGE_ERLAUBT:
        tage = 28
    bis = datetime.now(timezone.utc).date()
    if tage == 0:
        try:
            von = date.fromisoformat(seit)
        except ValueError:
            von = bis - timedelta(days=365 * 2)
        return {"tage": 0, "von": von.isoformat(), "bis": bis.isoformat(), "vorher": None}
    von = bis - timedelta(days=tage - 1)
    v_bis = von - timedelta(days=1)
    v_von = v_bis - timedelta(days=tage - 1)
    return {"tage": tage, "von": von.isoformat(), "bis": bis.isoformat(),
            "vorher": {"von": v_von.isoformat(), "bis": v_bis.isoformat()}}


def _zahl(x, ganz: bool = False):
    try:
        f = float(x or 0)
    except (TypeError, ValueError):
        f = 0.0
    return int(round(f)) if ganz else f


def _summe(z: dict | None) -> dict:
    """Eine Summenzeile in sprechende Felder umsetzen."""
    z = z or {}
    aufrufe = _zahl(z.get("views"), True)
    gewonnen, verloren = _zahl(z.get("subscribersGained"), True), _zahl(z.get("subscribersLost"), True)
    gefaellt, komm, teilen = (_zahl(z.get("likes"), True), _zahl(z.get("comments"), True),
                              _zahl(z.get("shares"), True))
    return {
        "aufrufe": aufrufe,
        "minuten": _zahl(z.get("estimatedMinutesWatched")),
        "sekunden": _zahl(z.get("averageViewDuration")),
        "gehalten": _zahl(z.get("averageViewPercentage")),
        "abos_neu": gewonnen, "abos_weg": verloren, "abos": gewonnen - verloren,
        "likes": gefaellt, "kommentare": komm, "teilen": teilen,
        # Wer schaut, tut auch etwas: Anteil der Aufrufe mit Like, Kommentar oder Teilen.
        "interaktion": round((gefaellt + komm + teilen) / aufrufe * 100, 2) if aufrufe else 0.0,
        "abo_je_tausend": round(gewonnen / aufrufe * 1000, 2) if aufrufe else 0.0,
    }


def _reihe(zeilen: list[dict]) -> list[dict]:
    reihe = []
    for z in zeilen:
        s = _summe(z)
        s["tag"] = str(z.get("day", ""))
        reihe.append(s)
    return sorted(reihe, key=lambda r: r["tag"])


def wochentage(reihe: list[dict]) -> list[dict]:
    """Durchschnittliche Aufrufe je Wochentag — wann läuft der Kanal am besten?"""
    namen = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    summen, anzahl = [0] * 7, [0] * 7
    for r in reihe:
        try:
            w = date.fromisoformat(r["tag"]).weekday()
        except ValueError:
            continue
        summen[w] += r["aufrufe"]
        anzahl[w] += 1
    return [{"tag": namen[i], "aufrufe": round(summen[i] / anzahl[i], 1) if anzahl[i] else 0.0,
             "tage": anzahl[i]} for i in range(7)]


# ---------- Videos ----------

def _dauer_s(iso: str) -> int:
    """ISO-8601-Dauer („PT1M5S“) in Sekunden."""
    import re
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    t, h, mi, s = (int(x or 0) for x in m.groups())
    return t * 86400 + h * 3600 + mi * 60 + s


async def _alle_videos(frisch: bool = False) -> list[dict]:
    """Alle Uploads mit ihren Gesamtzahlen (Data API). Neueste zuerst."""
    k = await _kanal(frisch)
    if not k["uploads"]:
        return []
    kennungen: list[str] = []
    seite = ""
    while len(kennungen) < 400:                   # 8 Seiten — mehr Videos hat der Kanal auf Jahre nicht
        p = {"part": "contentDetails", "playlistId": k["uploads"], "maxResults": 50}
        if seite:
            p["pageToken"] = seite
        liste = await _daten("playlistItems", p, frisch)
        kennungen += [(e.get("contentDetails") or {}).get("videoId", "") for e in liste.get("items") or []]
        seite = liste.get("nextPageToken", "")
        if not seite:
            break
    kennungen = [x for x in kennungen if x]

    videos = []
    for i in range(0, len(kennungen), 50):
        d = await _daten("videos", {"part": "snippet,statistics,contentDetails,status",
                                    "id": ",".join(kennungen[i:i + 50])}, frisch)
        for v in d.get("items") or []:
            sn, st = v.get("snippet") or {}, v.get("statistics") or {}
            dauer = _dauer_s((v.get("contentDetails") or {}).get("duration", ""))
            bilder = sn.get("thumbnails") or {}
            bild = (bilder.get("medium") or bilder.get("default") or {}).get("url", "")
            videos.append({
                "id": v.get("id", ""), "titel": sn.get("title", ""),
                "veroeffentlicht": str(sn.get("publishedAt", ""))[:10],
                "dauer": dauer, "kurz": 0 < dauer <= SHORT_MAX_S,
                "sichtbarkeit": (v.get("status") or {}).get("privacyStatus", ""),
                "bild": bild,
                "gesamt": {"aufrufe": _zahl(st.get("viewCount"), True),
                           "likes": _zahl(st.get("likeCount"), True),
                           "kommentare": _zahl(st.get("commentCount"), True)}})
    return sorted(videos, key=lambda v: v["veroeffentlicht"], reverse=True)


async def _video_bericht(z: dict, frisch: bool) -> list[dict]:
    """Der Video-Bericht mit Rückfall auf den bewährten kleinen Metriksatz."""
    basis = {"startDate": z["von"], "endDate": z["bis"], "dimensions": "video",
             "sort": "-views", "maxResults": MAX_VIDEOS}
    try:
        return await _bericht({**basis, "metrics": VIDEO_VOLL}, frisch)
    except ZugangFehler:
        raise
    except StudioFehler:
        return await _bericht({**basis, "metrics": VIDEO_KERN}, frisch)


def _median(werte: list[float]) -> float:
    w = sorted(werte)
    if not w:
        return 0.0
    m = len(w) // 2
    return float(w[m]) if len(w) % 2 else (w[m - 1] + w[m]) / 2


async def videos(tage: int = 28, frisch: bool = False) -> dict:
    """Alle Videos mit Zahlen für den Zeitraum UND insgesamt."""
    k = await _kanal(frisch)
    z = zeitraum(tage, k["seit"])
    liste, zeilen = await asyncio.gather(_alle_videos(frisch), _video_bericht(z, frisch))
    je_id = {r.get("video"): _summe(r) for r in zeilen}
    leer = _summe(None)
    for v in liste:
        v["zeitraum"] = je_id.get(v["id"], leer)
    aufrufe = [v["zeitraum"]["aufrufe"] for v in liste if v["zeitraum"]["aufrufe"]]
    mitte = _median(aufrufe)
    for v in liste:
        # Verhältnis zum mittleren Video des Zeitraums: 2,0 = doppelt so viele Aufrufe wie üblich.
        v["gegen_mitte"] = round(v["zeitraum"]["aufrufe"] / mitte, 2) if mitte else 0.0
    return {"zeitraum": z, "videos": liste, "mitte_aufrufe": mitte, "erneuert": _jetzt()}


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- Übersicht ----------

async def uebersicht(tage: int = 28, frisch: bool = False) -> dict:
    k = await _kanal(frisch)
    z = zeitraum(tage, k["seit"])
    await studio._zugriffstoken()                  # einmal erneuern, statt viermal gleichzeitig
    basis = {"startDate": z["von"], "endDate": z["bis"]}

    async def summe(von, bis):
        zeilen = await _bericht({"startDate": von, "endDate": bis, "metrics": SUMME}, frisch)
        return _summe(zeilen[0] if zeilen else None)

    async def vorher():
        if not z["vorher"]:
            return None
        return await summe(z["vorher"]["von"], z["vorher"]["bis"])

    jetzt, davor, tage_zeilen, alle = await asyncio.gather(
        summe(z["von"], z["bis"]), vorher(),
        _abschnitt(_bericht({**basis, "metrics": SUMME, "dimensions": "day", "sort": "day"}, frisch)),
        _abschnitt(videos(tage, frisch)))

    reihe = _reihe(tage_zeilen["zeilen"])
    liste = []
    if alle["ok"] and alle["zeilen"]:
        liste = alle["zeilen"]["videos"]
    im_zeitraum = [v for v in liste if v["veroeffentlicht"] >= z["von"]]
    spitzen = sorted(liste, key=lambda v: v["zeitraum"]["aufrufe"], reverse=True)[:3]
    letztes = liste[0]["veroeffentlicht"] if liste else ""
    return {
        "kanal": k, "zeitraum": z, "summe": jetzt, "davor": davor,
        "reihe": reihe, "reihe_ok": tage_zeilen["ok"], "reihe_fehler": tage_zeilen["fehler"],
        "wochentage": wochentage(reihe),
        "neue_videos": len(im_zeitraum),
        "letztes_video": letztes,
        "spitzen": [{"id": v["id"], "titel": v["titel"], "bild": v["bild"],
                     "aufrufe": v["zeitraum"]["aufrufe"], "gehalten": v["zeitraum"]["gehalten"]}
                    for v in spitzen if v["zeitraum"]["aufrufe"]],
        "erneuert": _jetzt(),
    }


# ---------- Publikum ----------

def _rangliste(zeilen: list[dict], schluessel: str, tabelle: dict | None = None,
               metrik: str = "views", grenze: int = 12) -> list[dict]:
    gesamt = sum(_zahl(z.get(metrik)) for z in zeilen) or 1.0
    aus = []
    for z in zeilen[:grenze]:
        wert = _zahl(z.get(metrik))
        roh = str(z.get(schluessel, ""))
        aus.append({"schluessel": roh, "name": _schoen(tabelle, roh) if tabelle is not None else roh,
                    "wert": int(round(wert)), "anteil": round(wert / gesamt * 100, 1),
                    "minuten": round(_zahl(z.get("estimatedMinutesWatched")), 1)})
    return aus


async def _liste(z: dict, dimension: str, tabelle: dict | None, frisch: bool, *,
                 metriken: str = "views,estimatedMinutesWatched", filter: str = "",
                 metrik: str = "views", grenze: int = 12, sortiert: str = "-views") -> list[dict]:
    p = {"startDate": z["von"], "endDate": z["bis"], "metrics": metriken,
         "dimensions": dimension, "sort": sortiert, "maxResults": 25}
    if filter:
        p["filters"] = filter
    return _rangliste(await _bericht(p, frisch), dimension, tabelle, metrik, grenze)


async def _alter(z: dict, frisch: bool, filter: str = "") -> list[dict]:
    p = {"startDate": z["von"], "endDate": z["bis"], "metrics": "viewerPercentage",
         "dimensions": "ageGroup,gender", "sort": "ageGroup,gender"}
    if filter:
        p["filters"] = filter
    zeilen = await _bericht(p, frisch)
    aus = []
    for r in zeilen:
        gruppe = str(r.get("ageGroup", "")).replace("age", "")
        aus.append({"alter": gruppe, "geschlecht": _schoen(GESCHLECHT, str(r.get("gender", ""))),
                    "anteil": round(_zahl(r.get("viewerPercentage")), 1)})
    return aus


async def publikum(tage: int = 28, frisch: bool = False) -> dict:
    k = await _kanal(frisch)
    z = zeitraum(tage, k["seit"])
    await studio._zugriffstoken()
    such = "insightTrafficSourceType==YT_SEARCH"
    (quellen, suche, laender, alter, geraete, systeme, abo, orte, teilen, inhalt) = await asyncio.gather(
        _abschnitt(_liste(z, "insightTrafficSourceType", QUELLEN, frisch)),
        _abschnitt(_liste(z, "insightTrafficSourceDetail", None, frisch, metriken="views",
                          filter=such, grenze=10)),
        _abschnitt(_liste(z, "country", None, frisch, grenze=10)),
        _abschnitt(_alter(z, frisch)),
        _abschnitt(_liste(z, "deviceType", GERAETE, frisch)),
        _abschnitt(_liste(z, "operatingSystem", BETRIEBSSYSTEM, frisch, metriken="views", grenze=8)),
        _abschnitt(_liste(z, "subscribedStatus", ABO, frisch)),
        _abschnitt(_liste(z, "insightPlaybackLocationType", ORTE, frisch, metriken="views")),
        _abschnitt(_liste(z, "sharingService", TEILEN, frisch, metriken="shares", metrik="shares",
                          sortiert="-shares", grenze=8)),
        _abschnitt(_liste(z, "creatorContentType", INHALT, frisch)))
    return {"zeitraum": z, "quellen": quellen, "suche": suche, "laender": laender, "alter": alter,
            "geraete": geraete, "systeme": systeme, "abo": abo, "orte": orte, "teilen": teilen,
            "inhalt": inhalt, "erneuert": _jetzt()}


# ---------- Ein Video ----------

def _einbruch(punkte: list[dict], dauer: int) -> dict | None:
    """Die Stelle, an der am meisten Zuschauer aufhören (größtes Gefälle zwischen zwei Messpunkten)."""
    groesst, stelle = 0.0, None
    for a, b in zip(punkte, punkte[1:]):
        fall = a["gesehen"] - b["gesehen"]
        if fall > groesst:
            groesst, stelle = fall, b
    if not stelle or groesst < 0.02:
        return None
    return {"sekunde": round(stelle["anteil"] * dauer, 1) if dauer else None,
            "anteil": stelle["anteil"], "verlust": round(groesst * 100, 1)}


async def video(kennung: str, frisch: bool = False) -> dict:
    """Ein Video im Detail — über seine gesamte Laufzeit, bis zu 120 Tage zurück."""
    alle = await _alle_videos(frisch)
    v = next((x for x in alle if x["id"] == kennung), None)
    if not v:
        raise StudioFehler("Dieses Video gibt es auf dem Kanal nicht.")
    heute = datetime.now(timezone.utc).date()
    start = max(date.fromisoformat(v["veroeffentlicht"] or heute.isoformat()), heute - timedelta(days=120))
    z = {"tage": (heute - start).days + 1, "von": start.isoformat(), "bis": heute.isoformat(), "vorher": None}
    ab_start = date.fromisoformat(v["veroeffentlicht"] or heute.isoformat()).isoformat()
    filt = f"video=={kennung}"
    await studio._zugriffstoken()

    async def gesamt():
        zeilen = await _bericht({"startDate": ab_start, "endDate": z["bis"], "metrics": SUMME,
                                 "filters": filt}, frisch)
        return _summe(zeilen[0] if zeilen else None)

    async def tage_reihe():
        zeilen = await _bericht({"startDate": z["von"], "endDate": z["bis"], "metrics": SUMME,
                                 "dimensions": "day", "sort": "day", "filters": filt}, frisch)
        return _reihe(zeilen)

    async def kurve():
        zeilen = await _bericht({"startDate": ab_start, "endDate": z["bis"],
                                 "metrics": "audienceWatchRatio,relativeRetentionPerformance",
                                 "dimensions": "elapsedVideoTimeRatio", "sort": "elapsedVideoTimeRatio",
                                 "filters": filt}, frisch)
        return [{"anteil": _zahl(r.get("elapsedVideoTimeRatio")),
                 "gesehen": _zahl(r.get("audienceWatchRatio")),
                 "relativ": _zahl(r.get("relativeRetentionPerformance"))} for r in zeilen]

    zz = {**z, "von": ab_start}
    summe, reihe, punkte, quellen, laender, abo, geraete = await asyncio.gather(
        _abschnitt(gesamt()), _abschnitt(tage_reihe()), _abschnitt(kurve()),
        _abschnitt(_liste(zz, "insightTrafficSourceType", QUELLEN, frisch, filter=filt)),
        _abschnitt(_liste(zz, "country", None, frisch, filter=filt, grenze=6)),
        _abschnitt(_liste(zz, "subscribedStatus", ABO, frisch, filter=filt)),
        _abschnitt(_liste(zz, "deviceType", GERAETE, frisch, filter=filt)))

    kurve_punkte = punkte["zeilen"]
    mittel_relativ = (sum(p["relativ"] for p in kurve_punkte) / len(kurve_punkte)) if kurve_punkte else 0.0
    return {
        "video": v, "zeitraum": {**z, "seit_veroeffentlichung": ab_start},
        "summe": summe["zeilen"] if summe["ok"] else None, "summe_fehler": summe["fehler"],
        "reihe": reihe["zeilen"], "reihe_ok": reihe["ok"], "reihe_fehler": reihe["fehler"],
        "kurve": kurve_punkte, "kurve_ok": punkte["ok"], "kurve_fehler": punkte["fehler"],
        # 0,5 = so wie ähnlich lange Videos; darüber halten Zuschauer besser durch.
        "kurve_relativ": round(mittel_relativ, 2) if kurve_punkte else None,
        "einbruch": _einbruch(kurve_punkte, v["dauer"]),
        "quellen": quellen, "laender": laender, "abo": abo, "geraete": geraete,
        "erneuert": _jetzt(),
    }
