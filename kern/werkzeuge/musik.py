"""Musik-Werkzeug — was gerade auf Spotify läuft.

Rein lesend. Steuern (Start, Pause, Weiter) wäre ein Schreibzugriff und käme,
wie beim Kalender, erst mit Rückfrage.
"""

from __future__ import annotations

from kern.dienste import netz, spotify

KONNEKTOR = "spotify"
MAX_WARTESCHLANGE = 10   # mehr auf einmal will niemand hören
MAX_ANZEIGE = 15         # so viele kommende Titel nennt sie höchstens

# „Ich höre nichts" ist nicht automatisch „zu leise" (Eike, 20.09.2026): steht das
# Gerät schon auf 70 %, liegt es woanders, und Aufdrehen macht es nur schlimmer.
# Darum entscheidet das Werkzeug anhand dieser Schwellen, nicht das Modell.
LEISE = 20               # darunter ist die Lautstärke die wahrscheinliche Ursache
LAUT_GENUG = 60          # darüber ist sie es sicher nicht

ERKLAERUNGEN = [
    {
        "name": "musik",
        "description": ("Sagt, was gerade auf Spotify läuft: Titel, Künstler, Album und wie weit es ist. "
                        "Benutzen bei Fragen wie 'Was läuft gerade?', 'Wie heißt das Lied?' oder "
                        "'Von wem ist das?'"),
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]


def _zeit(ms: int) -> str:
    s = max(0, int(ms)) // 1000
    return f"{s // 60}:{s % 60:02d}"


async def musik() -> str:
    try:
        stueck = await spotify.laeuft_gerade()
    except spotify.SpotifyFehler as exc:
        return f"Ich komme gerade nicht an Spotify: {exc}"
    except Exception as exc:
        return netz.nicht_erreichbar("Spotify", exc)
    if not stueck:
        return "Auf Spotify läuft gerade nichts."

    text = f"{'Läuft gerade' if stueck['laeuft'] else 'Pausiert'}: {stueck['titel']}"
    if stueck["wer"]:
        text += f" von {stueck['wer']}"
    if stueck["album"] and stueck["art"] != "episode":
        text += f" (Album {stueck['album']})"
    if stueck["dauer_ms"]:
        text += f" — {_zeit(stueck['position_ms'])} von {_zeit(stueck['dauer_ms'])}"
    if stueck["geraet"]:
        text += f", auf {stueck['geraet']}"
    if stueck.get("lautstaerke") is not None:
        text += f", Lautstärke {stueck['lautstaerke']} %"
    text += "."

    # Woraus läuft es? Playlist, Album, Künstler — mit Namen, wenn Spotify ihn hergibt.
    quelle = {"playlist": "der Playlist", "album": "dem Album", "artist": "den Titeln von",
              "show": "dem Podcast", "collection": "deinen gespeicherten Titeln"}.get(stueck["quelle_art"], "")
    if quelle:
        text += f" Läuft aus {quelle}"
        text += f" „{stueck['quelle_name']}“." if stueck["quelle_name"] else "."
    zusatz = []
    if stueck["zufall"]:
        zusatz.append("Zufallswiedergabe an")
    if stueck["wiederholung"] and stueck["wiederholung"] != "off":
        zusatz.append("Wiederholung: " + ("ein Titel" if stueck["wiederholung"] == "track" else "alles"))
    if zusatz:
        text += " " + ", ".join(zusatz) + "."
    return text


# ---------- Steuern ----------
# Bewusst OHNE Rückfrage, anders als beim Kalender: Pause, Weiter und ein Titel
# in der Warteschlange sind sofort umkehrbar, eine Rückfrage wäre nur lästig.
# Zwei Grenzen bleiben: das Schreibrecht auf der Freigaben-Seite
# (NUR_MIT_SCHREIBRECHT) und nichts aus fremdem Text.

FREMD_ABGELEHNT = ("Der Wunsch stammt aus fremdem Text (Mail, Brief oder Webseite). Daraus steuere ich "
                   "die Musik nicht — sag es mir selbst.")


async def _tun(was, erfolg: str, fremde_inhalte: bool) -> str:
    if fremde_inhalte:
        return FREMD_ABGELEHNT
    try:
        await was()
    except spotify.SpotifyFehler as exc:
        return str(exc)
    except Exception as exc:
        return netz.nicht_erreichbar("Spotify", exc)
    return erfolg


async def musik_pause(fremde_inhalte: bool = False) -> str:
    return await _tun(spotify.pause, "Pausiert.", fremde_inhalte)


async def musik_weiter(fremde_inhalte: bool = False) -> str:
    if fremde_inhalte:
        return FREMD_ABGELEHNT
    try:
        geraet = await spotify.weiter()
    except spotify.SpotifyFehler as exc:
        return str(exc)
    except Exception as exc:
        return netz.nicht_erreichbar("Spotify", exc)
    return f"Läuft weiter auf {geraet}." if geraet else "Läuft weiter."


async def musik_geraete() -> str:
    """Welche Geräte gibt es, und welche darf Friday benutzen?"""
    try:
        liste = await spotify.geraete()
    except spotify.SpotifyFehler as exc:
        return str(exc)
    except Exception as exc:
        return netz.nicht_erreichbar("Spotify", exc)
    if not liste:
        return "Spotify sieht gerade kein einziges Gerät."
    zeilen = [f"{g['name']} ({g['typ']})"
              + (f" — {g['lautstaerke']} %" if g.get("lautstaerke") is not None and g.get("regelbar")
                 else " — Lautstärke nicht regelbar" if not g.get("regelbar") else "")
              + (" — aktiv" if g["aktiv"] else "")
              + ("" if g["erlaubt"] else " — nicht freigegeben")
              for g in liste]
    return ("Geräte:\n" + "\n".join(zeilen)
            + "\n(Abspielen darf ich nur auf freigegebenen — das stellt Eike auf der Freigaben-Seite ein.)")


async def warteschlange_zeigen() -> str:
    """Was als Nächstes kommt — rein lesend.

    Fehlte bis zum 13.09.2026. Friday behauptete deshalb, Spotify lasse das
    nicht zu, und übersprang fünfmal blind, statt nachzusehen.
    """
    try:
        stand = await spotify.warteschlange_ansehen()
    except spotify.SpotifyFehler as exc:
        return str(exc)
    except Exception as exc:
        return netz.nicht_erreichbar("Spotify", exc)

    def nenn(s: dict) -> str:
        return f"{s['titel']} von {s['wer']}" if s["wer"] else s["titel"]

    if not stand["jetzt"] and not stand["naechste"]:
        return "Auf Spotify läuft gerade nichts, und die Warteschlange ist leer."
    zeilen = []
    if stand["jetzt"]:
        zeilen.append(f"Läuft gerade: {nenn(stand['jetzt'])}")
    if stand["naechste"]:
        zeilen.append("Als Nächstes:")
        zeilen += [f"{i}. {nenn(s)}" for i, s in enumerate(stand["naechste"][:MAX_ANZEIGE], 1)]
        if len(stand["naechste"]) > MAX_ANZEIGE:
            zeilen.append(f"… und {len(stand['naechste']) - MAX_ANZEIGE} weitere.")
    else:
        zeilen.append("Danach kommt nichts mehr.")
    return "\n".join(zeilen)


async def musik_naechster(fremde_inhalte: bool = False) -> str:
    return await _tun(spotify.naechster, "Nächster Titel.", fremde_inhalte)


async def musik_vorheriger(fremde_inhalte: bool = False) -> str:
    return await _tun(spotify.vorheriger, "Vorheriger Titel.", fremde_inhalte)


async def musik_geraet_waehlen(geraet: str) -> str:
    """Wiedergabe auf ein benanntes Gerät holen."""
    try:
        name = await spotify.auf_geraet(geraet)
    except spotify.SpotifyFehler as exc:
        return str(exc)
    except Exception as exc:
        return netz.nicht_erreichbar("Spotify", exc)
    return f"Wiedergabe läuft jetzt auf {name}."


async def warteschlange(titel: list | None = None) -> str:
    """Titel in die Warteschlange. Ohne Angabe: das, was gerade läuft, noch einmal."""
    # Jeder Wunsch ist {titel, kuenstler} — getrennt, weil Spotify mit Feldfiltern
    # genau sucht und sonst irgendeinen Song des Künstlers zurückgibt.
    wuensche = []
    for eintrag in (titel or [])[:MAX_WARTESCHLANGE]:
        if isinstance(eintrag, dict):
            t, k = str(eintrag.get("titel", "")).strip(), str(eintrag.get("kuenstler", "")).strip()
        else:
            t, k = str(eintrag).strip(), ""
        if t:
            wuensche.append((t, k))
    try:
        if not wuensche:
            laeuft = await spotify.laeuft_gerade()
            if not laeuft:
                return "Es läuft gerade nichts, und du hast keinen Titel genannt."
            wuensche = [(laeuft["titel"], laeuft["wer"])]
        umgeschaltet = ""
        gefunden, fehlt, schon = [], [], set()
        for wunsch, kuenstler in wuensche:
            treffer = await spotify.suchen(wunsch, kuenstler)
            if not treffer:
                fehlt.append(wunsch)
                continue
            # Nicht denselben Titel zweimal anhängen, nur weil zwei Wünsche
            # auf dieselbe Aufnahme führen (passierte am 14.09.2026).
            if treffer["uri"] in schon:
                fehlt.append(wunsch)
                continue
            schon.add(treffer["uri"])
            try:
                await spotify.zur_warteschlange(treffer["uri"])
            except spotify.SpotifyFehler as exc:
                # Erst versuchen, dann umschalten — nicht vorsorglich die
                # Geräteliste abfragen, das kostet bei jedem Titel einen Aufruf.
                if "kein Gerät aktiv" not in str(exc) or umgeschaltet:
                    raise
                umgeschaltet = await spotify.uebernehmen()
                await spotify.zur_warteschlange(treffer["uri"])
            gefunden.append(f"{treffer['titel']} von {treffer['wer']}" if treffer["wer"] else treffer["titel"])
    except spotify.SpotifyFehler as exc:
        return str(exc)
    except Exception as exc:
        return netz.nicht_erreichbar("Spotify", exc)
    if not gefunden:
        return "Nichts davon habe ich bei Spotify gefunden: " + ", ".join(fehlt) + "."
    text = ("In die Warteschlange: " + "; ".join(gefunden) + ".")
    if umgeschaltet:
        text += f" Wiedergabe auf {umgeschaltet} gestartet."
    if fehlt:
        text += " Nicht gefunden: " + ", ".join(fehlt) + "."
    return text


async def musik_jetzt(titel: str, kuenstler: str = "") -> str:
    """Einen Titel SOFORT spielen — für „spiel jetzt …“, nicht „häng … an“."""
    try:
        treffer = await spotify.suchen(titel.strip(), kuenstler.strip())
        if not treffer:
            return f"Das habe ich nicht bei Spotify gefunden: {titel.strip()}."
        umgeschaltet = ""
        try:
            await spotify.abspielen(treffer["uri"])
        except spotify.SpotifyFehler as exc:
            if "kein Gerät aktiv" not in str(exc):
                raise
            umgeschaltet = await spotify.uebernehmen()
            await spotify.abspielen(treffer["uri"])
    except spotify.SpotifyFehler as exc:
        return str(exc)
    except Exception as exc:
        return netz.nicht_erreichbar("Spotify", exc)
    nenn = f"{treffer['titel']} von {treffer['wer']}" if treffer["wer"] else treffer["titel"]
    return f"{nenn} läuft jetzt." + (f" Wiedergabe auf {umgeschaltet} gestartet." if umgeschaltet else "")


async def musik_playlist_abspielen(name: str) -> str:
    """Eine ganze Playlist SOFORT spielen — nicht ihre Titel einzeln anhängen."""
    try:
        treffer = await spotify.playlist_finden(name)
        if not treffer:
            return f"Eine Playlist namens „{name.strip()}“ finde ich bei dir nicht."
        gewaehlt = treffer[0]
        umgeschaltet = ""
        try:
            await spotify.playlist_abspielen(gewaehlt["id"])
        except spotify.SpotifyFehler as exc:
            if "kein Gerät aktiv" not in str(exc):
                raise
            umgeschaltet = await spotify.uebernehmen()
            await spotify.playlist_abspielen(gewaehlt["id"])
    except ValueError:
        return "Diese Playlist lässt sich nicht abspielen."
    except spotify.SpotifyFehler as exc:
        return str(exc)
    except Exception as exc:
        return netz.nicht_erreichbar("Spotify", exc)
    text = f"Die Playlist „{gewaehlt['name']}“ läuft jetzt." + (f" Wiedergabe auf {umgeschaltet} gestartet." if umgeschaltet else "")
    if len(treffer) > 1:
        text += " Es passen auch: " + ", ".join(p["name"] for p in treffer[1:4]) + "."
    return text


# ---------- Was Eike hört ----------
# Rein lesend, deshalb NICHT am Schreibrecht. Zweck (Eikes Wunsch vom
# 19.09.2026): Friday soll Empfehlungen aus seinem echten Geschmack ableiten,
# statt sie aus dem Modellwissen zu raten.

MAX_PLAYLISTS = 20       # mehr vorzulesen hat niemand etwas davon
MAX_TITEL = 25           # Titel aus einer Playlist


def _nenn(s: dict) -> str:
    return f"{s['titel']} von {s['wer']}" if s.get("wer") else s.get("titel", "")


async def _lesend(was, leer: str):
    """Holt eine Liste und übersetzt Fehler in einen vorlesbaren Satz."""
    try:
        return await was(), ""
    except spotify.SpotifyFehler as exc:
        return None, str(exc)
    except Exception as exc:
        return None, netz.nicht_erreichbar("Spotify", exc)


async def musik_playlists() -> str:
    """Welche Playlists hat Eike?"""
    liste, fehler = await _lesend(spotify.playlists, "")
    if fehler:
        return fehler
    if not liste:
        return "Du hast bei Spotify keine Playlists."
    zeilen = [f"{p['name']}" + (f" ({p['anzahl']} Titel)" if p.get("anzahl") else "")
              # Gefolgte fremde Listen kenntlich machen: in die sieht Spotify nicht hinein.
              + ("" if p["eigen"] else f" — von {p['besitzer']}" if p["besitzer"] else " — fremd")
              for p in liste[:MAX_PLAYLISTS]]
    text = f"{len(liste)} Playlists:\n" + "\n".join(zeilen)
    if len(liste) > MAX_PLAYLISTS:
        text += f"\n… und {len(liste) - MAX_PLAYLISTS} weitere."
    return text


async def musik_playlist(name: str) -> str:
    """Was steht in einer bestimmten Playlist?"""
    treffer, fehler = await _lesend(lambda: spotify.playlist_finden(name), "")
    if fehler:
        return fehler
    if not treffer:
        return f"Eine Playlist namens „{name}“ finde ich bei dir nicht."
    gewaehlt = treffer[0]
    # In fremde Listen lässt Spotify nicht hineinsehen (403, gemessen 20.09.2026).
    # Das ehrlich sagen und den Besitzer nennen, statt „geht nicht" zu melden.
    if not gewaehlt["eigen"]:
        wem = f"gehört {gewaehlt['besitzer']}" if gewaehlt["besitzer"] else "ist nicht deine"
        return (f"„{gewaehlt['name']}“ {wem} — du folgst ihr nur. In fremde Listen lässt Spotify "
                f"mich nicht hineinsehen, nur in deine eigenen.")
    inhalt, fehler = await _lesend(lambda: spotify.playlist_titel(gewaehlt["id"], MAX_TITEL), "")
    if fehler:
        return fehler
    titel = inhalt["titel"]
    if not titel:
        return f"Die Playlist „{gewaehlt['name']}“ ist leer."
    zeilen = [f"{i}. {_nenn(s)}" for i, s in enumerate(titel, 1)]
    text = f"„{gewaehlt['name']}“ ({inhalt['gesamt']} Titel):\n" + "\n".join(zeilen)
    if inhalt["gesamt"] > len(titel):
        text += f"\n… und {inhalt['gesamt'] - len(titel)} weitere."
    # Mehrere passende Listen: sagen, welche genommen wurde — nicht stillschweigend wählen.
    if len(treffer) > 1:
        text += ("\nEs passen auch: " + ", ".join(p["name"] for p in treffer[1:4]) + ".")
    return text


async def musik_zuletzt() -> str:
    """Was hat Eike zuletzt gehört?"""
    liste, fehler = await _lesend(spotify.zuletzt_gehoert, "")
    if fehler:
        return fehler
    if not liste:
        return "Spotify merkt sich gerade nichts, was du zuletzt gehört hast."
    # Dubletten zusammenfassen: dasselbe Lied dreimal hintereinander sagt nichts Neues.
    gesehen, zeilen = set(), []
    for s in liste:
        if s["uri"] in gesehen:
            continue
        gesehen.add(s["uri"])
        zeilen.append(f"{len(zeilen) + 1}. {_nenn(s)}")
    return "Zuletzt gehört:\n" + "\n".join(zeilen[:MAX_ANZEIGE])


async def musik_geschmack(zeitraum: str = "mittel") -> str:
    """Die meistgehörten Künstler und Titel — Grundlage für Empfehlungen."""
    zeitraum = zeitraum if zeitraum in spotify.ZEITRAEUME else "mittel"
    leute, fehler = await _lesend(lambda: spotify.lieblinge("kuenstler", zeitraum, 15), "")
    if fehler:
        return fehler
    lieder, fehler = await _lesend(lambda: spotify.lieblinge("titel", zeitraum, 15), "")
    if fehler:
        return fehler
    if not leute and not lieder:
        return "Spotify hat noch keine Auswertung, was du am meisten hörst."
    wann = {"kurz": "der letzten Wochen", "mittel": "der letzten Monate",
            "lang": "der letzten Jahre"}[zeitraum]
    teile = []
    if leute:
        teile.append(f"Meistgehörte Künstler {wann}: " + ", ".join(k["name"] for k in leute) + ".")
    if lieder:
        teile.append(f"Meistgehörte Titel {wann}:\n"
                     + "\n".join(f"{i}. {_nenn(s)}" for i, s in enumerate(lieder, 1)))
    return "\n".join(teile)


# ---------- Ton ----------
# Eikes Wunsch (20.09.2026): Sagt er „ich höre nichts", soll Friday NICHT reflexhaft
# aufdrehen. Steht das Gerät schon hoch, ist die Lautstärke nachweislich nicht die
# Ursache — dann wäre Lauterdrehen die falsche Antwort auf ein anderes Problem.


async def musik_ton_pruefen() -> str:
    """Warum hört Eike nichts? Nennt Gerät, Lautstärke und ein klares Urteil."""
    stand, fehler = await _lesend(spotify.laeuft_gerade, "")
    if fehler:
        return fehler
    if not stand:
        liste, fehler = await _lesend(spotify.geraete, "")
        if fehler:
            return fehler
        aktive = [g for g in (liste or []) if g["aktiv"]]
        if aktive:
            return (f"Auf Spotify läuft gerade nichts, aber {aktive[0]['name']} ist noch aktiv. "
                    "An der Lautstärke liegt es also nicht — es ist nichts gestartet.")
        return ("Auf Spotify läuft gerade nichts und kein Gerät ist aktiv. Das ist keine Frage "
                "der Lautstärke — es muss erst etwas gestartet werden.")

    wo = stand["geraet"] or "einem unbekannten Gerät"
    was = f"{stand['titel']} von {stand['wer']}" if stand["wer"] else stand["titel"]
    if not stand["laeuft"]:
        return (f"Spotify ist pausiert ({was}, auf {wo}). Deshalb hörst du nichts — "
                "das ist keine Frage der Lautstärke.")

    pegel = stand.get("lautstaerke")
    if not stand.get("regelbar") or pegel is None:
        return (f"Es läuft {was} auf {wo}. Die Lautstärke gibt dieses Gerät nicht heraus, ich kann "
                "sie also weder sagen noch stellen — die Regelung muss am Gerät selbst passieren.")
    if pegel <= 0:
        return f"Es läuft {was} auf {wo}, aber die Lautstärke steht auf 0 %. Das ist die Ursache."
    if pegel < LEISE:
        return (f"Es läuft {was} auf {wo}, die Lautstärke steht aber nur auf {pegel} %. "
                "Das ist vermutlich die Ursache — ich kann lauter machen.")
    if pegel < LAUT_GENUG:
        return (f"Es läuft {was} auf {wo}, Lautstärke {pegel} %. Das ist nicht laut, aber auch nicht "
                "auffällig leise. Prüf, ob der Ton am Gerät selbst stummgeschaltet ist oder ob du am "
                "richtigen Gerät stehst — ich kann auch lauter machen, wenn du willst.")
    return (f"Es läuft {was} auf {wo}, und die Lautstärke steht schon auf {pegel} %. An der liegt es "
            f"also nicht. Wahrscheinlicher: der Ton läuft auf {wo} statt dort, wo du bist, das Gerät "
            "ist stumm oder aus, oder die Verbindung hängt. Soll ich auf ein anderes Gerät umschalten?")


async def musik_lautstaerke(prozent: int, fremde_inhalte: bool = False) -> str:
    """Lautstärke stellen — nur auf Ansage, nie von selbst."""
    if fremde_inhalte:
        return FREMD_ABGELEHNT
    try:
        wert = int(prozent)
    except (TypeError, ValueError):
        return "Sag mir einen Wert zwischen 0 und 100."
    stand, _ = await _lesend(spotify.laeuft_gerade, "")
    vorher = (stand or {}).get("lautstaerke")
    if stand and not stand.get("regelbar"):
        return f"{stand['geraet'] or 'Dieses Gerät'} lässt die Lautstärke nicht von außen stellen."
    try:
        neu = await spotify.lautstaerke_setzen(wert)
    except spotify.SpotifyFehler as exc:
        return str(exc)
    except Exception as exc:
        return netz.nicht_erreichbar("Spotify", exc)
    wohin = f" auf {stand['geraet']}" if stand and stand.get("geraet") else ""
    if vorher is not None:
        return f"Lautstärke{wohin} von {vorher} % auf {neu} % gestellt."
    return f"Lautstärke{wohin} auf {neu} % gestellt."


# Der Fremdtext-Riegel gilt hier NICHT mehr (14.09.2026): er blockierte Eike
# selbst, sobald Friday im selben Zug etwas nachgeschlagen hatte — etwa um
# Songtitel zu finden. Musiksteuern ist sofort umkehrbar; der Riegel bleibt dort,
# wo etwas Bleibendes entsteht (merken, Termine, Erinnerungen).
for _f in (musik_pause, musik_weiter, musik_naechster, musik_vorheriger, musik_lautstaerke):
    _f.kennt_fremde_inhalte = True

ERKLAERUNGEN += [
    {"name": "musik_pause", "description": "Pausiert die Wiedergabe auf Spotify.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "musik_weiter", "description": "Setzt die pausierte Wiedergabe fort.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "musik_naechster", "description": "Springt zum nächsten Titel.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "musik_vorheriger", "description": "Springt zum vorherigen Titel.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "musik_geraete",
     "description": ("Zählt die Spotify-Geräte auf und sagt, welche freigegeben sind. Benutzen bei "
                     "'wo kannst du abspielen' oder wenn kein Gerät aktiv ist."),
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "warteschlange_zeigen",
     "description": ("Zeigt, was gerade läuft und welche Titel als Nächstes in der Warteschlange "
                     "stehen. Benutzen bei 'was kommt als Nächstes', 'was ist in der Warteschlange' "
                     "— und IMMER, bevor du gezielt Titel überspringst, damit du weißt, welche."),
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "warteschlange",
     "description": ("Setzt Titel auf die Spotify-Warteschlange. Wünscht Eike Musik zu einem Thema oder "
                     "einer Stimmung ('ein paar ruhige Songs', 'was von den 90ern'), suchst DU passende "
                     "Titel aus deinem Wissen aus. Titel und Künstler IMMER getrennt angeben — sonst "
                     "findet Spotify den falschen Song. Ohne Angabe wird der laufende Titel wiederholt. "
                     "Sage hinterher genau das weiter, was zurückkommt, auch was nicht gefunden wurde."),
     "parameters": {"type": "OBJECT", "properties": {
         "titel": {"type": "ARRAY", "description": "Die gewünschten Titel",
                   "items": {"type": "OBJECT", "properties": {
                       "titel": {"type": "STRING", "description": "Nur der Songtitel, z.B. Wonderwall"},
                       "kuenstler": {"type": "STRING", "description": "Nur der Künstler, z.B. Oasis"},
                   }, "required": ["titel"]}},
     }}},
    {"name": "musik_playlists",
     "description": ("Zählt Eikes eigene Spotify-Playlists auf, mit Titelzahl. Benutzen bei "
                     "'welche Playlists habe ich', 'was für Listen gibt es'."),
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "musik_playlist_abspielen",
     "description": ("Spielt eine ganze Playlist von Eike SOFORT ab — für 'spiel meine Playlist X', 'mach die "
                     "Playlist X an'. Das ist die richtige Wahl, wenn er eine PLAYLIST hören will (nicht "
                     "musik_jetzt oder warteschlange mit einzelnen Titeln daraus). Ersetzt, was gerade läuft."),
     "parameters": {"type": "OBJECT", "properties": {
         "name": {"type": "STRING", "description": "Name der Playlist, so wie Eike sie nennt"},
     }, "required": ["name"]}},
    {"name": "musik_playlist",
     "description": ("Zeigt, welche Titel in einer bestimmten Playlist von Eike stehen. Benutzen bei "
                     "'was ist in meiner Playlist X', und auch, um für eine Stimmung passende Titel "
                     "aus SEINEN Listen zu nehmen statt welche zu erfinden."),
     "parameters": {"type": "OBJECT", "properties": {
         "name": {"type": "STRING", "description": "Name der Playlist, so wie Eike sie nennt"},
     }, "required": ["name"]}},
    {"name": "musik_zuletzt",
     "description": ("Zeigt, was Eike zuletzt auf Spotify gehört hat, neueste zuerst. Benutzen bei "
                     "'was habe ich gestern gehört', 'wie hieß das Lied von vorhin' — und bevor du "
                     "etwas vorschlägst, damit du nicht dasselbe nochmal empfiehlst."),
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "musik_geschmack",
     "description": ("Nennt Eikes meistgehörte Künstler und Titel. Benutzen IMMER, bevor du ihm Musik "
                     "aussuchst ('spiel was Entspanntes', 'ein paar Lieder, die ich mag'), damit die "
                     "Vorschläge zu seinem echten Geschmack passen statt geraten zu sein."),
     "parameters": {"type": "OBJECT", "properties": {
         "zeitraum": {"type": "STRING", "description": "kurz (letzte Wochen), mittel (Monate), lang (Jahre)"},
     }}},
    {"name": "musik_ton_pruefen",
     "description": ("Prüft, WARUM Eike nichts hört: was läuft, auf welchem Gerät und wie laut. "
                     "IMMER zuerst benutzen, wenn er sagt 'ich höre nichts', 'da kommt kein Ton' oder "
                     "'ist das an?' — NICHT einfach lauter machen. Die Antwort sagt, ob die Lautstärke "
                     "die Ursache ist; sag sie genau so weiter."),
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "musik_lautstaerke",
     "description": ("Stellt die Lautstärke des aktiven Spotify-Geräts (0 bis 100 Prozent). Benutzen "
                     "bei 'mach lauter', 'leiser', 'stell auf 30'. Ohne ausdrücklichen Wunsch drehst "
                     "du NICHT auf — und nie, wenn musik_ton_pruefen sagt, dass es nicht daran liegt."),
     "parameters": {"type": "OBJECT", "properties": {
         "prozent": {"type": "INTEGER", "description": "Zielwert 0-100"},
     }, "required": ["prozent"]}},
    {"name": "musik_jetzt",
     "description": ("Spielt einen bestimmten Titel SOFORT ab — für 'spiel jetzt …', 'mach … an', 'ich will … hören'. "
                     "Ersetzt, was gerade läuft. Für eine ganze PLAYLIST nimmst du musik_playlist_abspielen. Für 'häng … an' oder Musik zu einer Stimmung nimmst du "
                     "warteschlange. Titel und Künstler IMMER getrennt angeben."),
     "parameters": {"type": "OBJECT", "properties": {
         "titel": {"type": "STRING", "description": "Nur der Songtitel, z.B. Fahrrad fahr'n"},
         "kuenstler": {"type": "STRING", "description": "Nur der Künstler, z.B. Max Raabe"},
     }, "required": ["titel"]}},
    {"name": "musik_geraet_waehlen",
     "description": ("Holt die Wiedergabe auf ein bestimmtes Gerät ('spiel auf dem Echo in der Küche'). "
                     "Nur freigegebene Geräte sind möglich; ist eines nicht freigegeben, sag das."),
     "parameters": {"type": "OBJECT", "properties": {
         "geraet": {"type": "STRING", "description": "Name des Geräts, z.B. Echo Eike"},
     }, "required": ["geraet"]}},
]

NUR_MIT_SCHREIBRECHT = {"musik_pause", "musik_weiter", "musik_naechster", "musik_vorheriger",
                        "warteschlange", "musik_geraet_waehlen", "musik_lautstaerke", "musik_jetzt",
                        "musik_playlist_abspielen"}

WERKZEUGE = {"musik": musik, "musik_geraete": musik_geraete,
             "musik_playlists": musik_playlists, "musik_playlist": musik_playlist,
             "musik_zuletzt": musik_zuletzt, "musik_geschmack": musik_geschmack,
             "musik_ton_pruefen": musik_ton_pruefen, "musik_lautstaerke": musik_lautstaerke,
             "warteschlange_zeigen": warteschlange_zeigen, "musik_pause": musik_pause,
             "musik_weiter": musik_weiter, "musik_naechster": musik_naechster,
             "musik_vorheriger": musik_vorheriger, "warteschlange": warteschlange,
             "musik_geraet_waehlen": musik_geraet_waehlen, "musik_jetzt": musik_jetzt,
             "musik_playlist_abspielen": musik_playlist_abspielen}
