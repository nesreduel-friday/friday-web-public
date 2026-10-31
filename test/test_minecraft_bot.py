"""Minecraft-Bot (Mindcraft): Status, Protokoll und Steuerung — ohne Netz, ohne Docker.

Der Bot läuft als Container auf hphome; Friday erreicht ihn über den bestehenden hphome-Helfer
(`kern/dienste/hphome.py`). Den Spielzustand liefert der MindServer — über socket.io, nicht HTTP
(gemessen am 29.10.2026, siehe `docs/LEHREN.md`). Hier wird beides ersetzt: der Helfer durch Attrappen,
die Verbindung durch eine, die vorbereitete Ereignisse abspielt.
"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kern import konnektoren
from kern.dienste import hphome, mindserver
from kern.dienste import minecraft_bot as dienst
from kern.werkzeuge import katalog

CONTAINER = "friday-mc-mindcraft-1"
LOG_KOPF = "Protokoll des Minecraft-Bots"

# So meldet der MindServer seine Bots (`agents-status`, gekürzt auf die Felder, die Friday liest).
BOTEN = [{"name": "Friday", "in_game": True, "viewerPort": 3000, "socket_connected": True}]


def _lauf(c):
    return asyncio.run(c)


def _zustand(**felder):
    container = {"name": CONTAINER, "zustand": "running", "gesundheit": "", "seit": 1_700_000_000,
                 "ports": [{"host": 3000, "container": 8080}], "host_netz": False,
                 "image": "friday-mc-mindcraft"}
    container.update(felder)
    return {"aktualisiert": 0.0, "container_ok": True, "container": [container]}


def _spiel(**felder):
    """Ein Eintrag aus `state-update` in der Form des echten MindServers (29.10.2026)."""
    spiel = {
        "name": "Friday",
        "gameplay": {"position": {"x": -495.5, "y": 69, "z": -2223.36}, "dimension": "overworld",
                     "gamemode": "survival", "health": 20, "hunger": 20, "biome": "plains",
                     "weather": "Clear"},
        "action": {"current": "autopilot:holz_sammeln", "kind": "acting", "isIdle": False},
        "inventory": {"counts": {"porkchop": 3}, "stacksUsed": 1, "totalSlots": 46,
                      "equipment": {"helmet": None, "mainHand": "porkchop"}},
    }
    spiel.update(felder)
    return spiel


class Fake:
    """Attrappe der MindServer-Verbindung: spielt die vorbereiteten Ereignisse der Reihe nach ab."""

    def __init__(self, ereignisse=None):
        self.ereignisse = list(ereignisse or [])
        self.gesendet = []
        self.geschlossen = False

    async def senden(self, ereignis, *daten):
        self.gesendet.append((ereignis, daten))

    async def empfangen(self, zeitlimit):
        if not self.ereignisse:
            raise mindserver.Fehler("Der MindServer hat nicht geantwortet.")
        return self.ereignisse.pop(0)

    async def schliessen(self):
        self.geschlossen = True


class Basis(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "FRIDAY_DATEN": tempfile.mkdtemp(), "MINECRAFT_BOT_CONTAINER": CONTAINER,
            "MINECRAFT_BOT_URL": ""})
        self.env.start()
        konnektoren.setzen("minecraft_bot", True)
        konnektoren.schreiben_setzen("minecraft_bot", True)

    def tearDown(self):
        self.env.stop()

    def _helfer(self, **felder):
        return mock.patch.object(hphome, "zustand_roh", return_value=_zustand(**felder))

    def _ohne_helfer(self):
        return mock.patch.object(hphome, "zustand_roh", return_value=None)

    def _mit_url(self, url="http://mindserver:8080"):
        return mock.patch.dict(os.environ, {"MINECRAFT_BOT_URL": url})

    def _mind(self, *ereignisse):
        """Eine Verbindung, die diese Ereignisse abspielt; gibt die Attrappe zurück."""
        fake = Fake(ereignisse)
        p = mock.patch.object(dienst, "_verbinden", mock.AsyncMock(return_value=fake))
        p.start()
        self.addCleanup(p.stop)
        return fake

    def _mind_kaputt(self, text="Der MindServer ist nicht erreichbar (OSError)."):
        p = mock.patch.object(dienst, "_verbinden", mock.AsyncMock(side_effect=mindserver.Fehler(text)))
        p.start()
        self.addCleanup(p.stop)

    def _ohne_verbindung(self):
        """Prüft, dass gar nicht erst verbunden wird; gibt die Attrappe zum Prüfen zurück."""
        verbinden = mock.AsyncMock()
        p = mock.patch.object(dienst, "_verbinden", verbinden)
        p.start()
        self.addCleanup(p.stop)
        return verbinden


def _status(boten):
    return ("agents-status", [boten])


def _update(zustand):
    return ("state-update", [zustand])


class Status(Basis):
    def test_ohne_helfer_ehrlich(self):
        with self._ohne_helfer():
            text = _lauf(dienst.zustand())
        self.assertIn("Helfer", text)
        self.assertIn("nicht", text)

    def test_laufender_container(self):
        # Ohne Port-Zuordnung im Containerzustand gibt es keine Adresse — dann wird nichts geraten.
        verbinden = self._ohne_verbindung()
        with self._helfer(ports=[]), self._mit_url(""):
            text = _lauf(dienst.zustand())
        verbinden.assert_not_called()
        self.assertIn("läuft", text)
        self.assertIn("MINECRAFT_BOT_URL", text)

    def test_gestoppter_container(self):
        with self._helfer(zustand="exited", ports=[]), self._mit_url(""):
            text = _lauf(dienst.zustand())
        self.assertIn("gestoppt", text)
        self.assertNotIn("läuft seit", text)

    def test_unbekannter_container(self):
        with self._helfer(name="irgendein-anderer"), self._mit_url(""):
            text = _lauf(dienst.zustand())
        self.assertIn(CONTAINER, text)
        self.assertIn("kenne ich", text)

    def test_kranker_container(self):
        with self._helfer(gesundheit="unhealthy", ports=[]), self._mit_url(""):
            text = _lauf(dienst.zustand())
        self.assertIn("nicht gesund", text)

    def test_laufzeit_in_richtiger_mehrzahl(self):
        self.assertEqual(dienst._dauer(86400), "1 Tag")
        self.assertEqual(dienst._dauer(3 * 86400), "3 Tagen")
        self.assertEqual(dienst._dauer(2 * 3600), "2 Stunden")
        self.assertEqual(dienst._dauer(90), "1 Minute")
        self.assertEqual(dienst._dauer(20), "wenigen Sekunden")

    def test_mindserver_meldet_boten(self):
        boten = [{"name": "Friday", "in_game": True}, {"name": "Friday2", "in_game": False}]
        self._mind(_status(boten))
        with self._helfer(), self._mit_url():
            text = _lauf(dienst.zustand())
        self.assertIn("Friday", text)
        self.assertIn("Friday2", text)
        self.assertIn("im Spiel", text)
        self.assertIn("nicht im Spiel", text)

    def test_mindserver_nicht_erreichbar(self):
        self._mind_kaputt()
        with self._helfer(), self._mit_url():
            text = _lauf(dienst.zustand())
        self.assertIn("läuft", text)                   # Container bleibt sichtbar
        self.assertIn("MindServer", text)
        self.assertIn("nicht", text)

    def test_leere_mindserver_antwort(self):
        self._mind(_status([]))
        with self._helfer(), self._mit_url():
            text = _lauf(dienst.zustand())
        self.assertIn("keinen laufenden Bot", text)

    def test_ohne_bot_im_spiel_kein_abonnement(self):
        fake = self._mind(_status([{"name": "Friday", "in_game": False}]))
        with self._helfer(), self._mit_url():
            text = _lauf(dienst.zustand())
        self.assertEqual(fake.gesendet, [])            # kein listen-to-agents ohne Bot im Spiel
        self.assertIn("nicht im Spiel", text)

    def test_verbindung_wird_immer_geschlossen(self):
        fake = self._mind(_status(BOTEN), _update({"Friday": _spiel()}))
        with self._helfer(), self._mit_url():
            _lauf(dienst.zustand())
        self.assertTrue(fake.geschlossen)


class LiveOrt(Basis):
    """Eike will den Bot-Status live statt aus Logzeilen geraten: Position, Leben, Hunger und die
    laufende Aktion kommen aus dem `state-update` des MindServers."""

    def test_status_zeigt_ort_leben_hunger_und_aktion(self):
        fake = self._mind(_status(BOTEN), _update({"Friday": _spiel()}))
        with self._helfer(), self._mit_url():
            text = _lauf(dienst.zustand())
        self.assertIn("listen-to-agents", [name for name, _ in fake.gesendet])
        for teil in ("-495,5", "69", "-2223,4", "overworld", "Leben 20/20", "Hunger 20/20",
                     "autopilot:holz_sammeln"):
            self.assertIn(teil, text)

    def test_status_ohne_zustand_bleibt_stehen(self):
        # Kein state-update (z. B. älterer MindServer): Der übrige Status bleibt trotzdem stehen.
        self._mind(_status(BOTEN))
        with self._helfer(), self._mit_url():
            text = _lauf(dienst.zustand())
        self.assertIn("im Spiel", text)
        self.assertNotIn("Leben", text)

    def test_flache_felder_werden_weiter_verstanden(self):
        # Eine andere MindServer-Fassung meldet die Werte ohne `gameplay`-Schachtel.
        self._mind(_status(BOTEN), _update({"Friday": {"position": {"x": 1.5, "y": 70, "z": 2},
                                                       "health": 18, "food": 15}}))
        with self._helfer(), self._mit_url():
            text = _lauf(dienst.zustand())
        self.assertIn("1,5", text)
        self.assertIn("Leben 18/20", text)
        self.assertIn("Hunger 15/20", text)

    def test_steuerzeichen_aus_der_aktion(self):
        self._mind(_status(BOTEN), _update({"Friday": _spiel(action={"current": "tu\ndies\x07"})}))
        with self._helfer(), self._mit_url():
            text = _lauf(dienst.zustand())
        self.assertIn("tut gerade: tu dies", text)


class Adresse(Basis):
    """Ohne `MINECRAFT_BOT_URL` wird die MindServer-Adresse aus der Konfiguration des Bot-Containers gelesen:
    Der hphome-Helfer liefert die veröffentlichte Port-Zuordnung (Host-Port → Container-Port). Der MindServer
    hört im Container auf 8080; ohne passende Zuordnung wird nichts geraten."""

    def test_aus_veroeffentlichtem_port(self):
        with self._helfer():
            self.assertEqual(dienst.mindserver_url(), "http://host.docker.internal:3000")

    def test_env_adresse_gewinnt(self):
        with self._helfer(), self._mit_url("http://mindcraft:8080"):
            self.assertEqual(dienst.mindserver_url(), "http://mindcraft:8080")

    def test_host_netz_nimmt_den_mindserver_port(self):
        with self._helfer(host_netz=True, ports=[]):
            self.assertEqual(dienst.mindserver_url(), "http://host.docker.internal:8080")

    def test_ohne_passenden_port_wird_nichts_geraten(self):
        with self._helfer(ports=[{"host": 25565, "container": 25565}]):
            self.assertEqual(dienst.mindserver_url(), "")

    def test_ohne_helfer_wird_nichts_geraten(self):
        with self._ohne_helfer():
            self.assertEqual(dienst.mindserver_url(), "")

    def test_status_nutzt_die_abgeleitete_adresse(self):
        self._mind(_status(BOTEN))
        with self._helfer():
            text = _lauf(dienst.zustand())
        self.assertIn("Friday", text)
        self.assertIn("im Spiel", text)

    def test_inventar_nutzt_die_abgeleitete_adresse(self):
        self._mind(_status(BOTEN), _update({"Friday": _spiel()}))
        with self._helfer():
            text = _lauf(dienst.inventar())
        self.assertIn("porkchop × 3", text)


class Inventar(Basis):
    def test_zeigt_die_gegenstaende_aus_der_zaehlung(self):
        # Die Form vom 29.10.2026 ist eine Zählung (Name → Anzahl), keine Liste von Stapeln.
        lager = {"counts": {"oak_log": 12, "stone": 64, "porkchop": 3}}
        self._mind(_status(BOTEN), _update({"Friday": _spiel(inventory=lager)}))
        with self._mit_url():
            text = _lauf(dienst.inventar())
        self.assertIn("Inventar von „Friday“", text)
        self.assertIn("stone × 64", text)
        self.assertIn("oak_log × 12", text)
        self.assertIn("porkchop × 3", text)
        self.assertLess(text.index("stone"), text.index("oak_log"))      # größte Menge zuerst

    def test_in_der_hand(self):
        self._mind(_status(BOTEN), _update({"Friday": _spiel()}))
        with self._mit_url():
            text = _lauf(dienst.inventar())
        self.assertIn("In der Hand: porkchop", text)

    def test_leeres_inventar(self):
        self._mind(_status(BOTEN), _update({"Friday": _spiel(inventory={"counts": {}})}))
        with self._mit_url():
            text = _lauf(dienst.inventar())
        self.assertIn("Inventar von „Friday“", text)
        self.assertIn("leer", text)

    def test_ohne_mindserver_adresse_ehrlich(self):
        verbinden = self._ohne_verbindung()
        with self._mit_url(""), self._ohne_helfer():
            text = _lauf(dienst.inventar())
        verbinden.assert_not_called()
        self.assertIn("MINECRAFT_BOT_URL", text)

    def test_mehrere_boten_ohne_namen_fragt_nach(self):
        boten = [{"name": "Friday", "in_game": True}, {"name": "Friday2", "in_game": True}]
        self._mind(_status(boten))
        with self._mit_url():
            text = _lauf(dienst.inventar())
        self.assertIn("Friday", text)
        self.assertIn("Friday2", text)
        self.assertIn("Welchen meinst du", text)

    def test_bot_nach_namen(self):
        boten = [{"name": "Friday", "in_game": True}, {"name": "Friday2", "in_game": True}]
        zustand = {"Friday": _spiel(), "Friday2": _spiel(inventory={"counts": {"bread": 2}})}
        self._mind(_status(boten), _update(zustand))
        with self._mit_url():
            text = _lauf(dienst.inventar("Friday2"))
        self.assertIn("„Friday2“", text)
        self.assertIn("bread × 2", text)
        self.assertNotIn("porkchop", text)

    def test_unbekannter_bot(self):
        self._mind(_status(BOTEN))
        with self._mit_url():
            text = _lauf(dienst.inventar("carla"))
        self.assertIn("carla", text)
        self.assertIn("meldet", text)

    def test_bot_nicht_im_spiel(self):
        self._mind(_status([{"name": "Friday", "in_game": False}]))
        with self._mit_url():
            text = _lauf(dienst.inventar())
        self.assertIn("nicht im Spiel", text)
        self.assertNotIn("×", text)

    def test_unerwartetes_format_wird_nicht_als_leer_ausgegeben(self):
        self._mind(_status(BOTEN), _update({"Friday": _spiel(inventory={"foo": "bar"})}))
        with self._mit_url():
            text = _lauf(dienst.inventar())
        self.assertIn("Format", text)
        self.assertNotIn("leer", text)

    def test_verbindungsfehler_ehrlich(self):
        self._mind_kaputt("Der MindServer ist nicht erreichbar (ConnectionRefusedError).")
        with self._mit_url():
            text = _lauf(dienst.inventar())
        self.assertIn("nicht erreichbar", text)
        self.assertIn("ConnectionRefusedError", text)


class Aufgabe(Basis):
    def test_aufgabe_geht_als_nachricht_an_den_bot(self):
        fake = self._mind(_status(BOTEN))
        with self._mit_url():
            text = _lauf(dienst.aufgabe("Hol Holz und komm zurück.", "Friday"))
        self.assertIn("geschickt", text)
        self.assertIn("Hol Holz und komm zurück.", text)
        self.assertEqual(fake.gesendet, [("send-message",
                                          ("Friday", {"from": "ADMIN",
                                                      "message": "Hol Holz und komm zurück."}))])

    def test_leere_aufgabe_wird_nicht_gesendet(self):
        verbinden = self._ohne_verbindung()
        with self._mit_url():
            text = _lauf(dienst.aufgabe("   "))
        verbinden.assert_not_called()
        self.assertIn("NICHT ERLEDIGT", text)
        self.assertIn("leer", text)

    def test_zu_lange_aufgabe_wird_nicht_gesendet(self):
        verbinden = self._ohne_verbindung()
        with self._mit_url():
            text = _lauf(dienst.aufgabe("x" * (dienst.MAX_AUFGABE + 1)))
        verbinden.assert_not_called()
        self.assertIn("zu lang", text)

    def test_bot_nicht_im_spiel(self):
        fake = self._mind(_status([{"name": "Friday", "in_game": False}]))
        with self._mit_url():
            text = _lauf(dienst.aufgabe("Hol Holz."))
        self.assertEqual(fake.gesendet, [])
        self.assertIn("nicht im Spiel", text)

    def test_ohne_mindserver_adresse(self):
        verbinden = self._ohne_verbindung()
        with self._mit_url(""), self._ohne_helfer():
            text = _lauf(dienst.aufgabe("Hol Holz."))
        verbinden.assert_not_called()
        self.assertIn("MINECRAFT_BOT_URL", text)

    def test_mehrere_boten_ohne_namen_fragt_nach(self):
        fake = self._mind(_status([{"name": "Friday", "in_game": True},
                                  {"name": "Friday2", "in_game": True}]))
        with self._mit_url():
            text = _lauf(dienst.aufgabe("Hol Holz."))
        self.assertEqual(fake.gesendet, [])
        self.assertIn("Welchen meinst du", text)

    def test_verbindungsfehler_wird_ehrlich_und_protokolliert(self):
        self._mind_kaputt()
        with self._mit_url():
            text = _lauf(dienst.aufgabe("Hol Holz.", "Friday"))
        self.assertIn("NICHT ERLEDIGT", text)
        datei = Path(os.environ["FRIDAY_DATEN"]) / "minecraft_bot" / "aktionen.jsonl"
        eintrag = json.loads(datei.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((eintrag["aktion"], eintrag["ok"]), ("aufgabe", False))

    def test_steuerzeichen_werden_entfernt(self):
        fake = self._mind(_status(BOTEN))
        with self._mit_url():
            _lauf(dienst.aufgabe("Hol\x00 Holz\x07."))
        self.assertEqual(fake.gesendet[0][1][1]["message"], "Hol Holz.")

    def test_protokoll_haelt_die_aufgabe_fest(self):
        self._mind(_status(BOTEN))
        with self._mit_url():
            _lauf(dienst.aufgabe("Hol Holz."))
        datei = Path(os.environ["FRIDAY_DATEN"]) / "minecraft_bot" / "aktionen.jsonl"
        eintrag = json.loads(datei.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(eintrag["aktion"], "aufgabe")
        self.assertTrue(eintrag["ok"])
        self.assertEqual(eintrag["bot"], "Friday")


class Logs(Basis):
    def test_holt_die_letzten_zeilen_und_bereinigt_sie(self):
        roh = "\x1b[32m[INFO]\x1b[0m Bot verbunden\n\n\x1b[31m[WARN]\x1b[0m Hunger\n\x00"
        with mock.patch.object(hphome, "logs", return_value={"ok": True, "text": roh}) as holen:
            text = _lauf(dienst.logs(zeilen=1))
        holen.assert_called_once_with(CONTAINER)
        self.assertIn(LOG_KOPF, text)
        self.assertIn("Hunger", text)
        self.assertNotIn("Bot verbunden", text)        # nur die letzte Zeile
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x00", text)

    def test_zeilen_werden_begrenzt(self):
        roh = "\n".join(f"Zeile {i}" for i in range(150))
        with mock.patch.object(hphome, "logs", return_value={"ok": True, "text": roh}):
            text = _lauf(dienst.logs(zeilen=999))
        self.assertIn(f"letzte {dienst.MAX_LOGZEILEN} Zeilen", text)
        self.assertIn("Zeile 149", text)
        self.assertNotIn("Zeile 0\n", text)

    def test_helfer_meldet_keine_zeilen_wie_leer(self):
        with mock.patch.object(hphome, "logs", return_value={"ok": True, "text": "(keine Zeilen)"}):
            text = _lauf(dienst.logs())
        self.assertIn("leer", text)
        self.assertNotIn(LOG_KOPF, text)

    def test_leeres_protokoll(self):
        with mock.patch.object(hphome, "logs", return_value={"ok": True, "text": "  \n\n"}):
            text = _lauf(dienst.logs())
        self.assertIn("leer", text)
        self.assertNotIn(LOG_KOPF, text)

    def test_helfer_fehlt(self):
        fehler = hphome.HphomeFehler("Der Helfer auf hphome läuft nicht — ohne ihn geht das nicht.", 503)
        with mock.patch.object(hphome, "logs", side_effect=fehler):
            text = _lauf(dienst.logs())
        self.assertIn("Helfer", text)
        self.assertNotIn(LOG_KOPF, text)               # Fehlertext ist kein fremder Inhalt

    def test_helfer_meldet_fehler(self):
        with mock.patch.object(hphome, "logs", return_value={"ok": False, "text": "docker logs scheiterte"}):
            text = _lauf(dienst.logs())
        self.assertIn("scheiterte", text)
        self.assertNotIn(LOG_KOPF, text)


class Steuern(Basis):
    def test_positivliste_und_alias(self):
        faelle = {"start": "start", "starte": "start", "einschalten": "start",
                  "stopp": "stopp", "stop": "stopp", "anhalten": "stopp",
                  "neustart": "neustart", "neu starten": "neustart", "restart": "neustart"}
        for wort, erwartet in faelle.items():
            with self.subTest(wort), mock.patch.object(
                    hphome, "container_aktion", return_value={"ok": True, "text": ""}) as tun:
                text = _lauf(dienst.steuern(wort))
            tun.assert_called_once_with(CONTAINER, erwartet)
            self.assertNotIn("NICHT ERLEDIGT", text)

    def test_unbekannte_aktion_wird_nicht_ausgefuehrt(self):
        for wort in ("exec rm -rf /", "nachricht an alle", "", None):
            with self.subTest(wort), mock.patch.object(hphome, "container_aktion") as tun:
                text = _lauf(dienst.steuern(wort))
            tun.assert_not_called()
            self.assertIn("NICHT ERLEDIGT", text)
            self.assertIn("starten, stoppen, neu starten", text)

    def test_protokoll_schreibt_jede_aktion(self):
        with mock.patch.object(hphome, "container_aktion", return_value={"ok": True, "text": ""}):
            _lauf(dienst.steuern("neustart"))
        datei = Path(os.environ["FRIDAY_DATEN"]) / "minecraft_bot" / "aktionen.jsonl"
        eintrag = json.loads(datei.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(eintrag["aktion"], "neustart")
        self.assertTrue(eintrag["ok"])
        self.assertEqual(eintrag["container"], CONTAINER)
        self.assertIn("zeit", eintrag)

    def test_protokoll_auch_bei_fehler(self):
        fehler = hphome.HphomeFehler("Es läuft schon eine Aktion — einen Moment.", 409)
        with mock.patch.object(hphome, "container_aktion", side_effect=fehler):
            text = _lauf(dienst.steuern("start"))
        self.assertIn("NICHT ERLEDIGT", text)
        datei = Path(os.environ["FRIDAY_DATEN"]) / "minecraft_bot" / "aktionen.jsonl"
        eintrag = json.loads(datei.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((eintrag["aktion"], eintrag["ok"]), ("start", False))
        self.assertIn("Aktion", eintrag["grund"])

    def test_helfer_meldet_fehlschlag(self):
        with mock.patch.object(hphome, "container_aktion",
                               return_value={"ok": False, "text": "docker start scheiterte"}):
            text = _lauf(dienst.steuern("start"))
        self.assertIn("NICHT ERLEDIGT", text)
        self.assertIn("scheiterte", text)

    def test_containername_aus_der_umgebung(self):
        with mock.patch.dict(os.environ, {"MINECRAFT_BOT_CONTAINER": "eigener-bot-1"}), \
                mock.patch.object(hphome, "container_aktion", return_value={"ok": True, "text": ""}) as tun:
            _lauf(dienst.steuern("start"))
        tun.assert_called_once_with("eigener-bot-1", "start")


class Auswahl(unittest.TestCase):
    def test_frage_nach_dem_bot_laedt_die_server_gruppe(self):
        from kern.werkzeuge import auswahl
        for text in ("Was macht der Minecraft-Bot?", "Was macht der Bot gerade?",
                     "Starte den Mindcraft-Bot neu."):
            with self.subTest(text):
                self.assertIn("minecraft_bot", auswahl.module_von(auswahl.waehlen([text])))


class ImKatalog(Basis):
    def _namen(self):
        return {e["name"] for e in katalog.gemini_tools()[0]["functionDeclarations"]}

    def test_nur_mit_freigabe_sichtbar(self):
        konnektoren.setzen("minecraft_bot", False)
        self.assertEqual({"minecraft_bot_status", "minecraft_bot_logs", "minecraft_bot_steuern"} & self._namen(), set())
        konnektoren.setzen("minecraft_bot", True)
        konnektoren.schreiben_setzen("minecraft_bot", False)
        sichtbar = self._namen()
        self.assertIn("minecraft_bot_status", sichtbar)
        self.assertIn("minecraft_bot_logs", sichtbar)
        self.assertNotIn("minecraft_bot_steuern", sichtbar)       # Schreibrecht fehlt
        konnektoren.schreiben_setzen("minecraft_bot", True)
        self.assertIn("minecraft_bot_steuern", self._namen())

    def test_steuern_ohne_schreibrecht_wird_abgelehnt(self):
        konnektoren.schreiben_setzen("minecraft_bot", False)
        with mock.patch.object(hphome, "container_aktion") as tun:
            r = _lauf(katalog.ausfuehren("minecraft_bot_steuern", {"aktion": "start"}))
        tun.assert_not_called()
        self.assertEqual(r["status"], "nicht")
        self.assertIn("Schreibrecht", r["fehler"])

    def test_steuern_nach_fremdem_text_nur_auf_eikes_wort(self):
        with mock.patch.object(hphome, "container_aktion", return_value={"ok": True, "text": ""}) as tun:
            r = _lauf(katalog.ausfuehren("minecraft_bot_steuern", {"aktion": "neustart"},
                                         fremde_inhalte=True, eingabe="Was macht der Bot gerade?"))
        tun.assert_not_called()
        self.assertEqual(r["status"], "nicht")
        self.assertIn("fremder Text", r["fehler"])
        with mock.patch.object(hphome, "container_aktion", return_value={"ok": True, "text": ""}) as tun:
            r = _lauf(katalog.ausfuehren("minecraft_bot_steuern", {"aktion": "neustart"},
                                         fremde_inhalte=True, eingabe="Starte den Bot bitte neu."))
        tun.assert_called_once_with(CONTAINER, "neustart")
        self.assertEqual(r["status"], "ok")

    def test_logs_zaehlen_als_fremder_text(self):
        self.assertTrue(katalog.liefert_fremdes("minecraft_bot_logs", {"ergebnis": LOG_KOPF + ":\nHallo"}))
        self.assertFalse(katalog.liefert_fremdes("minecraft_bot_logs",
                                                 {"ergebnis": "Ich komme gerade nicht an das Protokoll."}))
        self.assertFalse(katalog.liefert_fremdes("minecraft_bot_status", {"ergebnis": "Der Bot läuft."}))

    def test_status_und_logs_nehmen_keinen_containernamen(self):
        for name in ("minecraft_bot_status", "minecraft_bot_logs"):
            eintrag = next(e for m in katalog._MODULE for e in m.ERKLAERUNGEN if e["name"] == name)
            eigenschaften = (eintrag.get("parameters") or {}).get("properties") or {}
            self.assertNotIn("container", eigenschaften)
            self.assertNotIn("name", eigenschaften)
        steuern = next(e for m in katalog._MODULE for e in m.ERKLAERUNGEN
                       if e["name"] == "minecraft_bot_steuern")
        self.assertIn("aktion", steuern["parameters"]["properties"])

    def test_inventar_und_aufgabe_sind_sichtbar_aufgabe_nur_mit_schreibrecht(self):
        konnektoren.schreiben_setzen("minecraft_bot", False)
        sichtbar = self._namen()
        self.assertIn("minecraft_bot_inventar", sichtbar)
        self.assertNotIn("minecraft_bot_aufgabe", sichtbar)
        konnektoren.schreiben_setzen("minecraft_bot", True)
        self.assertIn("minecraft_bot_aufgabe", self._namen())

    def test_aufgabe_ohne_schreibrecht_wird_abgelehnt(self):
        konnektoren.schreiben_setzen("minecraft_bot", False)
        with mock.patch.object(dienst, "aufgabe", mock.AsyncMock(return_value="egal")) as auf:
            r = _lauf(katalog.ausfuehren("minecraft_bot_aufgabe", {"aufgabe": "Hol Holz."}))
        auf.assert_not_called()
        self.assertEqual(r["status"], "nicht")
        self.assertIn("Schreibrecht", r["fehler"])

    def test_aufgabe_nach_fremdem_text_nur_auf_eikes_wort(self):
        with mock.patch.object(dienst, "aufgabe", mock.AsyncMock(return_value="Geschickt.")) as auf:
            r = _lauf(katalog.ausfuehren("minecraft_bot_aufgabe", {"aufgabe": "Hol Holz."},
                                         fremde_inhalte=True, eingabe="Was steht im Log?"))
        auf.assert_not_called()
        self.assertEqual(r["status"], "nicht")
        self.assertIn("fremder Text", r["fehler"])
        with mock.patch.object(dienst, "aufgabe", mock.AsyncMock(return_value="Geschickt.")) as auf:
            r = _lauf(katalog.ausfuehren("minecraft_bot_aufgabe", {"aufgabe": "Hol Holz."},
                                         fremde_inhalte=True,
                                         eingabe="Sag dem Bot, er soll Holz holen."))
        auf.assert_called_once_with("Hol Holz.", "")
        self.assertEqual(r["status"], "ok")

    def test_inventar_zaehlt_als_fremder_text(self):
        self.assertTrue(katalog.liefert_fremdes("minecraft_bot_inventar",
                                                 {"ergebnis": "Inventar von „Friday“: 1 Art.\n- Stein × 64"}))
        self.assertFalse(katalog.liefert_fremdes(
            "minecraft_bot_inventar", {"ergebnis": "Ich kann das Inventar nicht lesen: Der MindServer fehlt."}))
        self.assertFalse(katalog.liefert_fremdes(
            "minecraft_bot_aufgabe", {"ergebnis": "Auftrag an „Friday“ geschickt."}))


class Modell(Basis):
    """Das Modell des Bots umstellen: nur Gemini oder DeepSeek, über den hphome-Helfer."""

    def test_zeigt_das_modell_aus_dem_helfer(self):
        for wert, erwartet in (("deepseek", "deepseek"), ("gemini", "gemini"), ("gpt-4", ""), ("", ""), (None, "")):
            with self.subTest(wert), mock.patch.object(
                    hphome, "zustand_roh", return_value={"mindcraft": {"modell": wert}}):
                self.assertEqual(dienst.botmodell(), erwartet)

    def test_ohne_helfer_ist_das_modell_unbekannt(self):
        with mock.patch.object(hphome, "zustand_roh", return_value=None):
            self.assertEqual(dienst.botmodell(), "")

    def test_umstellen_nur_gemini_oder_deepseek(self):
        for wert in ("gpt-4", "claude", "", None, "deepseek; rm -rf /"):
            with self.subTest(wert), mock.patch.object(hphome, "bot_modell") as tun:
                text = _lauf(dienst.botmodell_setzen(wert))
            tun.assert_not_called()
            self.assertIn("NICHT ERLEDIGT", text)
            self.assertIn("Gemini und DeepSeek", text)

    def test_umstellen_ruft_den_helfer_und_protokolliert(self):
        with mock.patch.object(hphome, "bot_modell",
                               return_value={"ok": True, "text": "Der Bot läuft jetzt auf DeepSeek und wurde neu gestartet."}) as tun:
            text = _lauf(dienst.botmodell_setzen("DeepSeek"))
        tun.assert_called_once_with("deepseek")
        self.assertIn("DeepSeek", text)
        self.assertNotIn("NICHT ERLEDIGT", text)
        datei = Path(os.environ["FRIDAY_DATEN"]) / "minecraft_bot" / "aktionen.jsonl"
        eintrag = json.loads(datei.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((eintrag["aktion"], eintrag["ok"]), ("modell", True))

    def test_umstellen_am_helfer_vorbei_ist_ehrlich(self):
        fehler = hphome.HphomeFehler("Der Helfer auf hphome läuft nicht — ohne ihn geht das nicht.", 503)
        with mock.patch.object(hphome, "bot_modell", side_effect=fehler):
            text = _lauf(dienst.botmodell_setzen("deepseek"))
        self.assertIn("NICHT ERLEDIGT", text)
        self.assertIn("Helfer", text)

    def test_helfer_meldet_fehlschlag(self):
        with mock.patch.object(hphome, "bot_modell",
                               return_value={"ok": False, "text": "Kein DeepSeek-Schlüssel hinterlegt"}):
            text = _lauf(dienst.botmodell_setzen("deepseek"))
        self.assertIn("NICHT ERLEDIGT", text)
        self.assertIn("Schlüssel", text)


class Routen(Basis):
    """Nur Eikes Tipp in der App: POST /api/konnektoren/minecraft_bot/modell."""

    def _client(self):
        from fastapi.testclient import TestClient
        from kern.main import app
        return TestClient(app)

    def test_stellt_das_modell_um(self):
        with mock.patch("kern.main.minecraftbotdienst.botmodell_setzen",
                        new=mock.AsyncMock(return_value="Der Bot läuft jetzt auf DeepSeek und wurde neu gestartet.")), \
                mock.patch("kern.main.minecraftbotdienst.botmodell", return_value="deepseek"):
            r = self._client().post("/api/konnektoren/minecraft_bot/modell", json={"modell": "deepseek"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True, "modell": "deepseek",
                                    "text": "Der Bot läuft jetzt auf DeepSeek und wurde neu gestartet."})

    def test_nur_bei_eingeschaltetem_konnektor(self):
        konnektoren.setzen("minecraft_bot", False)
        r = self._client().post("/api/konnektoren/minecraft_bot/modell", json={"modell": "deepseek"})
        self.assertEqual(r.status_code, 409)

    def test_unbekannter_konnektor_ist_404(self):
        r = self._client().post("/api/konnektoren/gibtsnicht/modell", json={"modell": "deepseek"})
        self.assertEqual(r.status_code, 404)


class Konnektor(Basis):
    def test_eintrag(self):
        eintrag = konnektoren.KONNEKTOREN["minecraft_bot"]
        self.assertTrue(eintrag.get("ohne_zugang"))
        self.assertTrue(eintrag.get("schreibbar"))
        self.assertEqual(set(eintrag["werkzeuge"]),
                         {"minecraft_bot_status", "minecraft_bot_logs", "minecraft_bot_steuern",
                          "minecraft_bot_inventar", "minecraft_bot_aufgabe"})
        self.assertIn("minecraft_bot", {k["id"] for k in konnektoren.uebersicht()})


if __name__ == "__main__":
    unittest.main()
