"""Spotify — Friday sieht, was gerade läuft.

Anders als IServ oder iCloud gibt es hier kein Passwort, sondern OAuth: Eike
erlaubt den Zugriff einmal bei Spotify, danach arbeitet Friday mit einem
Token, den sie selbst erneuert.

Bewusst ohne `spotipy`: die Bibliothek ist synchron und bringt eine eigene
Token-Dateiverwaltung mit, die hier im Weg wäre. Nötig sind genau drei
Aufrufe — Anmeldeadresse bauen, Code gegen Token tauschen, Token erneuern —
plus ein GET für den laufenden Titel. Bei CalDAV war eine Bibliothek den
Aufwand wert, hier nicht.

Zugangsdaten: Client ID und Client Secret aus dem Spotify-Entwicklerbereich,
abgelegt wie bei den anderen Konnektoren (`kern.konnektoren`). Der Token
liegt in `data/spotify_token.json` (Rechte 600) und verlässt den Server nie.

Nur lesend: Friday sieht den laufenden Titel. Steuern (Start, Pause, Wechsel)
wäre ein Schreibzugriff und käme, wie beim Kalender, erst mit Rückfrage.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from base64 import b64encode
from urllib.parse import urlencode

import httpx

from kern import config, konnektoren
from kern.dienste import netz

log = logging.getLogger("friday.spotify")

KONNEKTOR = "spotify"
ANMELDUNG = "https://accounts.spotify.com/authorize"
TOKEN = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
# Eike wollte ausdrücklich ALLE Rechte, damit er nie wieder neu verbinden muss
# (14.09.2026). Das schließt schreibende ein — Playlists ändern, Bibliothek
# anfassen, folgen. Rechte allein tun nichts: Friday kann nur, wofür hier ein
# Werkzeug steht, und das ist bislang nur `musik` (lesend). Jedes schreibende
# Werkzeug bekommt wie beim Kalender eine Rückfrage.
RECHTE = " ".join([
    # lesen
    "user-read-playback-state", "user-read-currently-playing", "user-read-playback-position",
    "user-read-recently-played", "user-top-read", "user-read-email", "user-read-private",
    "user-library-read", "user-follow-read",
    "playlist-read-private", "playlist-read-collaborative",
    # steuern und schreiben
    "user-modify-playback-state", "user-library-modify",
    "playlist-modify-private", "playlist-modify-public",
    "user-follow-modify", "ugc-image-upload",
    # Wiedergabe über das Spotify-SDK
    "streaming", "app-remote-control",
])
ZEITLIMIT = 15

_staat: str = ""          # gegen untergeschobene Rückläufer (CSRF)
_namen: dict[str, str] = {}   # Playlist-/Album-Namen zwischenspeichern
_ich: str = ""                # Eikes eigene Spotify-Kennung (einmal geholt)


class SpotifyFehler(RuntimeError):
    """Vorlesbar: sagt, was Eike tun muss."""


def zugang() -> tuple[str, str]:
    z = konnektoren.zugang(KONNEKTOR)
    if not z:
        raise SpotifyFehler("Für Spotify sind noch keine Client ID und kein Client Secret eingetragen.")
    return z


def rueckadresse() -> str:
    """Muss im Spotify-Entwicklerbereich EXAKT so eingetragen sein."""
    return config.basis_url().rstrip("/") + "/api/spotify/rueck"


def _tokendatei():
    return config.daten_ordner() / "spotify_token.json"


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
        "scope": RECHTE, "state": _staat, "show_dialog": "false"})


def _kopf() -> dict:
    kennung, geheim = zugang()
    return {"Authorization": "Basic " + b64encode(f"{kennung}:{geheim}".encode()).decode()}


async def code_einloesen(code: str, staat: str) -> None:
    """Den Rückläufer von Spotify in einen dauerhaften Token tauschen."""
    if not _staat or staat != _staat:
        raise SpotifyFehler("Die Antwort von Spotify passt nicht zur Anfrage. Bitte neu verbinden.")
    async with netz.client(timeout=ZEITLIMIT) as client:
        antwort = await client.post(TOKEN, headers=_kopf(), data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": rueckadresse()})
    if antwort.status_code != 200:
        raise SpotifyFehler(f"Spotify gibt keinen Zugang heraus ({antwort.status_code}: {antwort.text[:120]}).")
    d = antwort.json()
    if not d.get("refresh_token"):
        raise SpotifyFehler("Spotify hat keinen dauerhaften Zugang mitgeschickt.")
    _token_schreiben({"refresh_token": d["refresh_token"], "access_token": d.get("access_token", ""),
                      "gilt_bis": time.time() + int(d.get("expires_in", 3600)) - 60})
    log.info("Mit Spotify verbunden")


async def _zugriffstoken() -> str:
    """Gültiger Token — wird selbstständig erneuert, wenn er abgelaufen ist."""
    daten = _token_lesen()
    if not daten.get("refresh_token"):
        raise SpotifyFehler("Friday ist noch nicht mit Spotify verbunden. Auf der Freigaben-Seite verbinden.")
    if daten.get("access_token") and time.time() < daten.get("gilt_bis", 0):
        return daten["access_token"]
    async with netz.client(timeout=ZEITLIMIT) as client:
        antwort = await client.post(TOKEN, headers=_kopf(), data={
            "grant_type": "refresh_token", "refresh_token": daten["refresh_token"]})
    if antwort.status_code != 200:
        raise SpotifyFehler(f"Der Spotify-Zugang gilt nicht mehr ({antwort.status_code}). Bitte neu verbinden.")
    d = antwort.json()
    daten.update({"access_token": d.get("access_token", ""),
                  "gilt_bis": time.time() + int(d.get("expires_in", 3600)) - 60})
    # Spotify schickt manchmal einen neuen dauerhaften Token mit.
    if d.get("refresh_token"):
        daten["refresh_token"] = d["refresh_token"]
    _token_schreiben(daten)
    return daten["access_token"]


async def _kontextname(client: httpx.AsyncClient, kopf: dict, kontext: dict) -> str:
    """Der Name der Playlist oder des Albums. Spotify schickt im Kontext nur eine
    Kennung mit (geprüft 14.09.2026) — der Name kostet einen zweiten Aufruf, der
    hier zwischengespeichert wird."""
    adresse = kontext.get("href") or ""
    if not adresse:
        return ""
    if adresse in _namen:
        return _namen[adresse]
    try:
        antwort = await client.get(adresse, headers=kopf, params={"fields": "name"})
        name = antwort.json().get("name", "") if antwort.status_code == 200 else ""
    except Exception:
        name = ""
    _namen[adresse] = name
    return name


def _cover(stueck: dict) -> str:
    """Das Titelbild in etwa 300 px — groß genug für die Übersicht, klein genug fürs Handy.
    Nur https: Die Adresse landet im Browser der Übersicht."""
    bilder = ((stueck.get("album") or {}).get("images")
              or stueck.get("images") or (stueck.get("show") or {}).get("images") or [])
    bilder = [b for b in bilder if str(b.get("url", "")).startswith("https://")]
    if not bilder:
        return ""
    return min(bilder, key=lambda b: abs((b.get("width") or 300) - 300))["url"]


def _uris(stueck: dict) -> dict:
    """Adressen zum Öffnen in der Spotify-App: Titel, Album (bei Podcasts die Show), erster Künstler."""
    kuenstler = (stueck.get("artists") or [{}])[0] or {}
    sammlung = (stueck.get("album") or stueck.get("show") or {})
    return {"uri": stueck.get("uri") or "", "album_uri": sammlung.get("uri") or "",
            "artist_uri": kuenstler.get("uri") or ""}


async def laeuft_gerade() -> dict | None:
    """Der laufende Titel samt Zusammenhang — oder None, wenn nichts läuft.

    `/me/player` statt `/me/player/currently-playing`: liefert in EINEM Aufruf
    zusätzlich Zufallswiedergabe, Wiederholung und Gerät.
    """
    token = await _zugriffstoken()
    async with netz.client(timeout=ZEITLIMIT) as client:
        antwort = await client.get(f"{API}/me/player",
                                   headers={"Authorization": f"Bearer {token}"},
                                   params={"additional_types": "track,episode"})
    if antwort.status_code == 204 or not antwort.content:
        return None                     # nichts aktiv — das ist keine Störung
    if antwort.status_code == 401:
        raise SpotifyFehler("Der Spotify-Zugang wurde zurückgezogen. Bitte neu verbinden.")
    if antwort.status_code != 200:
        raise SpotifyFehler(f"Spotify antwortet mit {antwort.status_code}.")
    d = antwort.json()
    stueck = d.get("item") or {}
    if not stueck:
        return None
    if stueck.get("type") == "episode":
        wer = (stueck.get("show") or {}).get("name", "")
        album = ""
    else:
        wer = ", ".join(k.get("name", "") for k in stueck.get("artists") or [] if k.get("name"))
        album = (stueck.get("album") or {}).get("name", "")
    kontext = d.get("context") or {}
    name = ""
    if kontext:
        async with netz.client(timeout=ZEITLIMIT) as client:
            name = await _kontextname(client, {"Authorization": f"Bearer {token}"}, kontext)
    return {"titel": stueck.get("name", ""), "wer": wer, "album": album, "cover": _cover(stueck), **_uris(stueck),
            "laeuft": bool(d.get("is_playing")), "position_ms": d.get("progress_ms") or 0,
            "dauer_ms": stueck.get("duration_ms") or 0,
            "geraet": ((d.get("device") or {}).get("name") or ""),
            "geraet_art": ((d.get("device") or {}).get("type") or ""),
            "lautstaerke": (d.get("device") or {}).get("volume_percent"),
            "regelbar": bool((d.get("device") or {}).get("supports_volume", True)),
            "art": stueck.get("type", "track"),
            "quelle_art": kontext.get("type", ""), "quelle_name": name,
            "zufall": bool(d.get("shuffle_state")), "wiederholung": d.get("repeat_state", "off")}


# ---------- Steuern ----------
# Alle Steuerbefehle antworten mit 204 ohne Rumpf. Die Fehler sind so
# aussagekräftig, dass Friday sie weitersagen kann, statt nur "ging nicht".

# Was eine 404 bedeutet, hängt vom Befehl ab. Bei Pause/Weiter/Nächster heißt
# sie „kein Gerät aktiv". Beim Umschalten (PUT /me/player) heißt sie dagegen
# „das ZIELgerät nimmt gerade nicht an" — gemessen am 13.09.2026: Umschalten auf
# „Echo Eike" ergab 404, während auf PC_VON_EIKE nachweislich Musik lief. Die
# alte Sammelmeldung schickte Eike in eine Schleife, die nie klappen konnte.
KEIN_GERAET = "Es ist gerade kein Gerät aktiv. Spiel kurz etwas ab, dann geht es."


async def _befehl(methode: str, pfad: str, params: dict | None = None, rumpf: dict | None = None,
                  wenn_404: str = KEIN_GERAET) -> None:
    token = await _zugriffstoken()
    async with netz.client(timeout=ZEITLIMIT) as client:
        antwort = await client.request(methode, f"{API}{pfad}",
                                       headers={"Authorization": f"Bearer {token}"},
                                       params=params or {}, json=rumpf)
    if antwort.status_code in (200, 202, 204):
        return
    if antwort.status_code == 404:
        raise SpotifyFehler(wenn_404)
    if antwort.status_code == 403:
        raise SpotifyFehler("Spotify lässt das nicht zu — Steuern über andere Programme geht nur mit Premium.")
    if antwort.status_code == 401:
        raise SpotifyFehler("Der Spotify-Zugang wurde zurückgezogen. Bitte neu verbinden.")
    if antwort.status_code == 429:
        raise SpotifyFehler("Zu viele Anfragen an Spotify. Gleich nochmal.")
    raise SpotifyFehler(f"Spotify antwortet mit {antwort.status_code}.")


# ---------- Geräte ----------
# Eike hat viele Geräte, darunter Echo Dots, auf denen NICHTS starten soll.
# Deshalb entscheidet nicht Friday, welche in Frage kommen, sondern er: nur
# angekreuzte Geräte dürfen Ziel einer Wiedergabe werden.

def _geraetedatei():
    return config.daten_ordner() / "spotify_geraete.json"


def erlaubte() -> list[dict]:
    try:
        d = json.loads(_geraetedatei().read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def erlaubte_setzen(liste: list[dict]) -> None:
    sauber = [{"id": str(g.get("id", ""))[:100], "name": str(g.get("name", ""))[:80]}
              for g in liste if g.get("id")]
    # Atomar und 600 wie der Token daneben — bis 14.09.2026 als einzige 644.
    ziel = _geraetedatei()
    ziel.parent.mkdir(parents=True, exist_ok=True)
    neu = ziel.with_suffix(".neu")
    fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(sauber, f, ensure_ascii=False)
    os.chmod(neu, 0o600)
    os.replace(neu, ziel)


async def geraete() -> list[dict]:
    """Alle Geräte, die Spotify gerade kennt — mit Vermerk, ob freigegeben."""
    token = await _zugriffstoken()
    async with netz.client(timeout=ZEITLIMIT) as client:
        antwort = await client.get(f"{API}/me/player/devices",
                                   headers={"Authorization": f"Bearer {token}"})
    if antwort.status_code != 200:
        raise SpotifyFehler(f"Die Geräteliste kam nicht ({antwort.status_code}).")
    frei = {g["id"] for g in erlaubte()}
    # `supports_volume` ist nicht überall wahr: Eikes „Badezimmer" meldet false
    # (gemessen 20.09.2026). Dort ist die Lautstärke weder lesbar noch stellbar.
    return [{"id": g.get("id", ""), "name": g.get("name", ""), "typ": g.get("type", ""),
             "aktiv": bool(g.get("is_active")), "erlaubt": g.get("id", "") in frei,
             "lautstaerke": g.get("volume_percent"),
             "regelbar": bool(g.get("supports_volume", True)),
             "eingeschraenkt": bool(g.get("is_restricted"))}
            for g in antwort.json().get("devices") or []]


ECHO_ZWEITER_VERSUCH_S = 2.0


async def _umschalten(ziel: dict, wenn_404: str) -> None:
    """Die Wiedergabe auf `ziel` holen — bei 404 ein zweites Mal.

    Eike hat es Friday dreimal erklärt (Gespräche 139, 152, 223, 16.–21.09.2026):
    Ein Echo, auf dem nichts lief, lehnt den ERSTEN Umschaltversuch mit 404 ab und
    nimmt den zweiten ein, zwei Sekunden später an — der erste weckt ihn offenbar.
    Jedes Mal kam „starte erst etwas über Alexa“, und erst sein „versuch es einfach
    nochmal“ half. Das macht jetzt der Kern selbst."""
    try:
        await _befehl("PUT", "/me/player", rumpf={"device_ids": [ziel["id"]], "play": True},
                      wenn_404=wenn_404)
    except SpotifyFehler as exc:
        if str(exc) != wenn_404:
            raise
        await asyncio.sleep(ECHO_ZWEITER_VERSUCH_S)
        await _befehl("PUT", "/me/player", rumpf={"device_ids": [ziel["id"]], "play": True},
                      wenn_404=wenn_404)


async def _erlaubtes_ziel() -> dict | None:
    """Das erste freigegebene Gerät, das gerade erreichbar ist."""
    for g in await geraete():
        if g["erlaubt"]:
            return g
    return None


async def uebernehmen() -> str:
    """Wiedergabe auf ein freigegebenes Gerät holen. Gibt dessen Namen zurück."""
    ziel = await _erlaubtes_ziel()
    if not ziel:
        if not erlaubte():
            raise SpotifyFehler("Es ist kein Gerät aktiv, und du hast noch keines freigegeben. "
                                "Auf der Freigaben-Seite unter Spotify die Geräte ankreuzen.")
        raise SpotifyFehler("Es ist kein Gerät aktiv, und keines deiner freigegebenen Geräte ist "
                            "gerade erreichbar.")
    await _umschalten(ziel, f"„{ziel['name']}“ nimmt die Wiedergabe auch beim zweiten Versuch nicht an. "
                            "Bei Echo-Geräten dort einmal kurz etwas starten, danach geht es.")
    return ziel["name"]


async def pause() -> None:
    await _befehl("PUT", "/me/player/pause")


async def weiter() -> str:
    """Fortsetzen. Ohne aktives Gerät wird auf ein freigegebenes umgeschaltet;
    der Rückgabewert nennt es dann, sonst leer."""
    try:
        await _befehl("PUT", "/me/player/play")
        return ""
    except SpotifyFehler as exc:
        if "kein Gerät aktiv" not in str(exc):
            raise
        return await uebernehmen()


async def lautstaerke_setzen(prozent: int, geraet_id: str = "") -> int:
    """Lautstärke des aktiven (oder eines benannten) Geräts. Gibt den Wert zurück.

    Spotify nimmt nur ganze Prozent von 0 bis 100. Ein 403 heißt hier meist nicht
    „kein Premium", sondern „dieses Gerät lässt sich nicht regeln" — deshalb eine
    eigene Meldung.
    """
    wert = max(0, min(100, int(prozent)))
    params = {"volume_percent": wert}
    if geraet_id:
        params["device_id"] = geraet_id
    try:
        await _befehl("PUT", "/me/player/volume", params=params)
    except SpotifyFehler as exc:
        if "Premium" in str(exc):
            raise SpotifyFehler("Dieses Gerät lässt die Lautstärke nicht von außen stellen.") from exc
        raise
    return wert


async def naechster() -> None:
    await _befehl("POST", "/me/player/next")


async def vorheriger() -> None:
    await _befehl("POST", "/me/player/previous")


def _knapp(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", (text or "").casefold())


def _passt(wunsch: str, gefunden: str) -> bool:
    """Ist der Treffer wirklich der gesuchte Titel?

    Spotify liefert bei einem Titel, den es nicht gibt, irgendeinen Song des
    Künstlers: „BENGALOS Ikkimel" ergab „Ikkimel die Geile" (gemessen
    14.09.2026), und das landete dann in der Warteschlange. Deshalb wird der
    Treffer gegengeprüft, statt blind dem ersten zu glauben.
    """
    w, g = _knapp(wunsch), _knapp(gefunden)
    # Beide Seiten müssen etwas hergeben. Ohne die zweite Prüfung galt ein
    # leerer Fundtext als Treffer auf ALLES: `"" in w` ist wahr. Bei Playlists
    # aus lauter Emoji („🪩") war der Name nach `_knapp` genau das (20.09.2026).
    if not w.strip() or not g.strip():
        return False
    if w.strip() in g or g.strip() in w:
        return True
    lang = {x for x in w.split() if len(x) >= 4}
    return bool(lang & set(g.split()))


async def _suchlauf(client, kopf: dict, frage: str) -> list[dict]:
    antwort = await client.get(f"{API}/search", headers=kopf,
                               params={"q": frage, "type": "track", "limit": 5})
    if antwort.status_code != 200:
        raise SpotifyFehler(f"Die Suche bei Spotify ging nicht ({antwort.status_code}).")
    return ((antwort.json().get("tracks") or {}).get("items") or [])


async def suchen(titel: str, kuenstler: str = "") -> dict | None:
    """Einen Titel finden — genau, nicht ungefähr.

    Erst mit Feldfiltern (`track:` / `artist:`), die Spotify streng nimmt; erst
    danach frei, und dann nur, wenn der Titel wirklich passt.
    """
    token = await _zugriffstoken()
    kopf = {"Authorization": f"Bearer {token}"}
    genau = f'track:"{titel}"' + (f' artist:"{kuenstler}"' if kuenstler else "")
    frei = f"{titel} {kuenstler}".strip()
    async with netz.client(timeout=ZEITLIMIT) as client:
        for frage in (genau, frei):
            for t in await _suchlauf(client, kopf, frage):
                if not _passt(titel, t.get("name", "")):
                    continue
                wer = ", ".join(k.get("name", "") for k in t.get("artists") or [] if k.get("name"))
                # Auch den Künstler gegenprüfen, nicht nur den Titel: „Stille Wasser
                # von Dorfrocker" lieferte „Stille Wasser von Schrotthagen" (gemessen
                # 13.09.2026) — Titel passte, Künstler war ein ganz anderer.
                if kuenstler and not _passt(kuenstler, wer):
                    continue
                return {"uri": t.get("uri", ""), "titel": t.get("name", ""), "wer": wer}
    return None


async def auf_geraet(name: str) -> str:
    """Wiedergabe auf ein benanntes Gerät holen — nur wenn es freigegeben ist."""
    liste = await geraete()
    gesucht = (name or "").casefold().strip()
    treffer = [g for g in liste if gesucht and gesucht in g["name"].casefold()]
    if not treffer:
        raise SpotifyFehler(f"Ein Gerät namens „{name}“ sehe ich nicht.")
    erlaubt = [g for g in treffer if g["erlaubt"]]
    if not erlaubt:
        raise SpotifyFehler(f"„{treffer[0]['name']}“ ist nicht freigegeben. "
                            "Auf der Freigaben-Seite unter Spotify ankreuzen.")
    ziel = erlaubt[0]
    await _umschalten(ziel, f"„{ziel['name']}“ steht zwar in der Liste, nimmt die Wiedergabe aber auch "
                            "beim zweiten Versuch nicht an. Starte kurz etwas über Alexa, dann schalte "
                            "ich um.")
    return ziel["name"]


async def abspielen(uri: str) -> None:
    """Diesen Titel sofort spielen (ersetzt, was gerade läuft)."""
    await _befehl("PUT", "/me/player/play", rumpf={"uris": [uri]})


_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9]{10,40}$")


async def playlist_abspielen(playlist_id: str) -> None:
    """Die GANZE Playlist spielen (als Kontext: „Nächster“ geht in der Liste weiter). Ersetzt, was gerade läuft."""
    if not _PLAYLIST_ID.match(playlist_id or ""):
        raise ValueError(playlist_id)
    await _befehl("PUT", "/me/player/play", rumpf={"context_uri": f"spotify:playlist:{playlist_id}"})


async def zur_warteschlange(uri: str) -> None:
    await _befehl("POST", "/me/player/queue", {"uri": uri})


async def warteschlange_ansehen() -> dict:
    """Was als Nächstes kommt.

    Spotify gibt das sehr wohl her. Am 13.09.2026 behauptete Friday, das ginge
    nicht („Spotify lässt das nicht zu"), und übersprang deshalb fünfmal blind,
    statt nachzusehen — dabei fehlte nur dieses Werkzeug. An Eikes Konto
    gemessen: 200 mit zwölf kommenden Titeln.
    """
    token = await _zugriffstoken()
    async with netz.client(timeout=ZEITLIMIT) as client:
        antwort = await client.get(f"{API}/me/player/queue",
                                   headers={"Authorization": f"Bearer {token}"})
    if antwort.status_code == 204 or not antwort.content:
        return {"jetzt": None, "naechste": []}
    if antwort.status_code == 401:
        raise SpotifyFehler("Der Spotify-Zugang wurde zurückgezogen. Bitte neu verbinden.")
    if antwort.status_code != 200:
        raise SpotifyFehler(f"Spotify antwortet mit {antwort.status_code}.")
    daten = antwort.json()

    def stueck(t: dict) -> dict:
        return {"titel": t.get("name", ""), "uri": t.get("uri", ""),
                "wer": ", ".join(k.get("name", "") for k in t.get("artists") or [] if k.get("name"))}

    laeuft = daten.get("currently_playing") or None
    return {"jetzt": stueck(laeuft) if laeuft else None,
            "naechste": [stueck(t) for t in (daten.get("queue") or []) if t]}


# ---------- Was Eike hört ----------
# Bis zum 20.09.2026 kannte Friday nur den laufenden Titel und riet Empfehlungen
# aus dem Modellwissen zusammen. Eike wollte, dass sie seine Playlists und den
# Verlauf lesen darf ("dann kannst du bessere Empfehlungen geben"). Die Rechte
# dafür lagen seit dem 14.09.2026 schon im Token — es fehlten nur die Werkzeuge.
#
# An Eikes Konto gemessen (20.09.2026), weil die Felder NICHT der Doku folgen:
#   · `/me/playlists` liefert die Titelzahl als `items: {href, total}`,
#     nicht als `tracks: {total}`. Beides wird gelesen, falls es zurückwandert.
#   · `/playlists/{id}/tracks` antwortet mit 403. Der Weg ist
#     `/playlists/{id}/items`, und der Eintrag heißt dort `item`, nicht `track`.
#   · Künstler haben KEINE `genres` und keine `popularity` mehr — auch nicht
#     über `/artists/{id}`. Empfehlungen nach Genre sind darüber nicht möglich.
ZEITRAEUME = {"kurz": "short_term", "mittel": "medium_term", "lang": "long_term"}


async def _lesen(pfad: str, params: dict | None = None) -> dict:
    """Ein lesender Aufruf mit denselben klaren Fehlern wie `_befehl`."""
    token = await _zugriffstoken()
    async with netz.client(timeout=ZEITLIMIT) as client:
        antwort = await client.get(f"{API}{pfad}", headers={"Authorization": f"Bearer {token}"},
                                   params=params or {})
    if antwort.status_code == 204 or not antwort.content:
        return {}
    if antwort.status_code == 200:
        return antwort.json()
    if antwort.status_code == 401:
        raise SpotifyFehler("Der Spotify-Zugang wurde zurückgezogen. Bitte neu verbinden.")
    if antwort.status_code == 403:
        raise SpotifyFehler("Spotify gibt diese Liste nicht mehr heraus.")
    if antwort.status_code == 404:
        raise SpotifyFehler("Das gibt es bei Spotify nicht (mehr).")
    if antwort.status_code == 429:
        raise SpotifyFehler("Zu viele Anfragen an Spotify. Gleich nochmal.")
    raise SpotifyFehler(f"Spotify antwortet mit {antwort.status_code}.")


def _stueck(t: dict) -> dict:
    """Ein Titel so, wie Friday ihn vorliest."""
    t = t or {}
    return {"titel": t.get("name", ""), "uri": t.get("uri", ""),
            "wer": ", ".join(k.get("name", "") for k in t.get("artists") or [] if k.get("name")),
            "art": t.get("type", "track")}


async def playlists() -> list[dict]:
    """Eikes Playlists. Ohne Beschreibungstext: der stammt bei geteilten Listen
    von Dritten und hat in Fridays Kopf nichts zu suchen."""
    d = await _lesen("/me/playlists", {"limit": 50})
    ich = await eigene_kennung()
    raus = []
    for p in d.get("items") or []:
        if not p:
            continue
        zaehler = p.get("tracks") or p.get("items") or {}
        besitzer = p.get("owner") or {}
        raus.append({"id": p.get("id", ""), "name": p.get("name", ""), "uri": p.get("uri", ""),
                     "anzahl": zaehler.get("total") if isinstance(zaehler, dict) else None,
                     "besitzer": besitzer.get("display_name") or "",
                     # 20 von Eikes 43 Listen sind gefolgte fremde (gemessen 20.09.2026).
                     # Deren Inhalt gibt Spotify nicht heraus — das muss Friday wissen,
                     # damit sie es sagt, statt eine leere Liste zu behaupten.
                     "eigen": bool(ich) and besitzer.get("id", "") == ich})
    return raus


def _name_passt(wunsch: str, name: str) -> bool:
    """Passt ein Playlist-Name zum Wunsch?

    Bewusst strenger als `_passt` (das ist für Songtitel gebaut): „Schlagerparty
    1998" traf damit die Listen „L" und „🪩💃🏼🕺🏻🔥" — ein Buchstabe steckt in
    fast jedem Wort, und Emoji-Namen sind nach `_knapp` leer. Hier muss ein
    ganzes Wort von mindestens vier Zeichen übereinstimmen (20.09.2026).
    """
    w, g = _knapp(wunsch).strip(), _knapp(name).strip()
    if not w or not g:
        return False
    if w == g:
        return True
    # Enthaltensein nur, wenn die kürzere Seite lang genug ist: Eikes Liste heißt
    # schlicht „L", und ein „l" steckt in fast jedem Wunsch („Schlagerparty").
    if min(len(w), len(g)) >= 4 and (w in g or g in w):
        return True
    return bool({x for x in w.split() if len(x) >= 4} & {x for x in g.split() if len(x) >= 4})


async def eigene_kennung() -> str:
    """Wem gehören die Listen? Fremde gibt Spotify nicht her (403, gemessen)."""
    global _ich
    if not _ich:
        _ich = (await _lesen("/me")).get("id", "")
    return _ich


async def playlist_finden(name: str) -> list[dict]:
    """Alle Playlists, die zum Wunsch passen — genaue Treffer zuerst.

    Nicht blind die erste nehmen (dieselbe Falle wie bei `suchen`): Eike hat 43
    Listen, darunter mehrere mit ähnlichem Namen.
    """
    gesucht = _knapp(name).strip()
    if not gesucht:
        return []
    treffer = []
    for p in await playlists():
        k = _knapp(p["name"]).strip()
        if k and k == gesucht:
            rang = 0
        elif k and len(gesucht) >= 4 and gesucht in k:
            rang = 1
        elif _name_passt(name, p["name"]):
            rang = 2
        else:
            continue
        # Bei gleichem Rang zuerst die eigenen: in fremde sieht Spotify nicht hinein.
        treffer.append(((rang, 0 if p["eigen"] else 1), p))
    treffer.sort(key=lambda x: x[0])
    return [p for _, p in treffer]


async def playlist_titel(playlist_id: str, grenze: int = 30) -> dict:
    """Die Titel einer Playlist. Über `/items`, nicht `/tracks` (403, gemessen)."""
    d = await _lesen(f"/playlists/{playlist_id}/items",
                     {"limit": max(1, min(int(grenze), 50)),
                      "fields": "total,items(item(name,uri,type,artists(name)))"})
    titel = [_stueck(e.get("item")) for e in d.get("items") or [] if e and e.get("item")]
    return {"gesamt": d.get("total", len(titel)), "titel": [t for t in titel if t["titel"]]}


async def zuletzt_gehoert(grenze: int = 20) -> list[dict]:
    """Was zuletzt lief — neueste zuerst. Spotify zählt nur zu Ende Gehörtes mit."""
    d = await _lesen("/me/player/recently-played", {"limit": max(1, min(int(grenze), 50))})
    raus = []
    for e in d.get("items") or []:
        if not e or not e.get("track"):
            continue
        s = _stueck(e["track"])
        s["zeit"] = e.get("played_at", "")
        raus.append(s)
    return raus


async def lieblinge(art: str = "titel", zeitraum: str = "mittel", grenze: int = 20) -> list[dict]:
    """Die meistgehörten Titel oder Künstler.

    `genres` liefert Spotify nicht mehr mit (gemessen 20.09.2026), deshalb bleibt
    es bei Namen — die reichen, damit das Modell den Geschmack einschätzt.
    """
    pfad = "/me/top/artists" if art == "kuenstler" else "/me/top/tracks"
    d = await _lesen(pfad, {"limit": max(1, min(int(grenze), 50)),
                            "time_range": ZEITRAEUME.get(zeitraum, "medium_term")})
    if art == "kuenstler":
        return [{"name": k.get("name", ""), "uri": k.get("uri", "")}
                for k in d.get("items") or [] if k and k.get("name")]
    return [s for s in (_stueck(t) for t in d.get("items") or [] if t) if s["titel"]]


async def pruefen() -> str:
    if not verbunden():
        return "Zugangsdaten sind da, aber Friday ist noch nicht mit Spotify verbunden."
    stueck = await laeuft_gerade()
    return "Mit Spotify verbunden. " + ("Gerade läuft nichts." if not stueck
                                        else f"Gerade läuft: {stueck['titel']}.")
