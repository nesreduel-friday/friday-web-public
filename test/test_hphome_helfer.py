"""hphome-Helfer (läuft auf dem Host): Zustand sammeln, Aktionen, Riegel.

Ohne docker, ohne sudo, ohne echtes /proc: Der Helfer wird direkt geladen und sein `ausfuehren`
durch eine Nachahmung ersetzt. Geprüft wird vor allem, dass aus der Auftragsdatei nichts als
Befehl durchschlagen kann."""

import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HELFER_DATEI = Path(__file__).resolve().parent.parent / "host-helfer" / "hphome_helfer.py"

INSPECT = [
    {"Name": "/jellyfin", "Config": {"Image": "jellyfin/jellyfin"},
     "State": {"Status": "running", "Running": True, "StartedAt": "2026-09-22T10:00:00.123456789Z",
               "Health": {"Status": "healthy"}},
     "HostConfig": {"NetworkMode": "bridge", "RestartPolicy": {"Name": "unless-stopped"}},
     "NetworkSettings": {"Ports": {"8096/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8096"},
                                                {"HostIp": "::", "HostPort": "8096"}]}}},
    {"Name": "/seminar-ki", "Config": {"Image": "seminar-ki:local"},
     "State": {"Status": "exited", "Running": False, "StartedAt": "2026-09-01T10:00:00Z"},
     "HostConfig": {"NetworkMode": "bridge", "RestartPolicy": {"Name": "no"}},
     "NetworkSettings": {"Ports": {}}},
    {"Name": "/adguardhome", "Config": {"Image": "adguard/adguardhome"},
     "State": {"Status": "running", "Running": True, "StartedAt": "2026-09-22T10:00:00Z"},
     "HostConfig": {"NetworkMode": "host", "RestartPolicy": {"Name": "unless-stopped"}},
     "NetworkSettings": {"Ports": {}}},
]


_ORDNER = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def tiefer_baum(wurzel: Path, tiefe: int) -> None:
    """Eine Kette `wurzel/a/a/…` mit `tiefe` Ebenen, über dir_fd gebaut (kein PATH_MAX, kein chdir)."""
    wurzel.mkdir()
    fd = os.open(wurzel, _ORDNER)
    try:
        for _ in range(tiefe):
            os.mkdir("a", dir_fd=fd)
            kind = os.open("a", _ORDNER, dir_fd=fd)
            os.close(fd)
            fd = kind
        os.close(os.open("x", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=fd))
    finally:
        os.close(fd)


def baum_weg(pfad: Path) -> None:
    """Test-Aufräumen beliebig tiefer Bäume: ohne Rekursion, höchstens zwei offene Ordner (über '..' zurück)."""
    try:
        if not os.path.isdir(pfad) or os.path.islink(pfad):
            os.unlink(pfad)
            return
    except FileNotFoundError:
        return
    os.chmod(pfad, 0o700)
    fd, namen = os.open(pfad, _ORDNER), []
    try:
        while True:
            eintraege = os.listdir(fd)
            if eintraege:
                n = eintraege[0]
                st = os.stat(n, dir_fd=fd, follow_symlinks=False)
                if (st.st_mode & 0o170000) == 0o040000:
                    kind = os.open(n, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.chmod(f"/proc/self/fd/{kind}", 0o700)
                    os.close(kind)
                    kind = os.open(n, _ORDNER, dir_fd=fd)
                    os.close(fd)
                    fd = kind
                    namen.append(n)
                else:
                    os.unlink(n, dir_fd=fd)
                continue
            if not namen:
                break
            eltern = os.open("..", _ORDNER, dir_fd=fd)
            os.close(fd)
            fd = eltern
            os.rmdir(namen.pop(), dir_fd=fd)
    finally:
        os.close(fd)
    os.rmdir(pfad)


def offene_fds() -> int | None:
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return None


def helfer_laden(daten: str):
    with mock.patch.dict(os.environ, {"FRIDAY_DATEN": daten}):
        spec = importlib.util.spec_from_file_location(f"hphome_helfer_{id(daten)}", HELFER_DATEI)
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
    return modul


class Basis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.daten = Path(self.tmp.name)
        p = mock.patch.dict(os.environ, {"FRIDAY_DATEN": str(self.daten)})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.h = helfer_laden(str(self.daten))
        self.addCleanup(lambda: getattr(self.h, "_ordner_schliessen", lambda: None)())   # gepinnte Ordner-fds
        self.h.MIN_ABSTAND_S = 0
        self.aufrufe: list[list[str]] = []

    def befehle(self, antworten: dict | None = None):
        """docker & Co. nachspielen: der erste passende Präfix gewinnt, alles andere schlägt fehl."""
        antworten = antworten or {}

        def falsch(befehl, zeitlimit=20):
            self.aufrufe.append(list(befehl))
            for muster, ergebnis in antworten.items():
                if list(befehl[:len(muster)]) == list(muster):
                    return ergebnis(befehl) if callable(ergebnis) else ergebnis
            return 127, "", "nicht nachgespielt"
        self.h.ausfuehren = falsch


class Hilfen(Basis):
    def test_bytes(self):
        self.assertEqual(self.h._bytes("136.5MiB"), 136.5 * 1024 ** 2)
        self.assertEqual(self.h._bytes("1.95GB"), 1.95e9)
        self.assertEqual(self.h._bytes("0B"), 0)
        self.assertEqual(self.h._bytes("kaputt"), 0)
        self.assertEqual(self.h._bytes(None), 0)

    def test_epoch(self):
        self.assertEqual(self.h._epoch("2026-09-26T19:27:30.873643442Z"), 1790450850)
        self.assertIsNone(self.h._epoch("0001-01-01T00:00:00Z"))
        self.assertIsNone(self.h._epoch(""))
        self.assertIsNone(self.h._epoch("gestern"))


class Host(Basis):
    def _proc(self):
        proc = self.daten / "proc"
        proc.mkdir()
        (proc / "uptime").write_text("12345.67 999.0\n")
        (proc / "loadavg").write_text("0.50 0.40 0.30 1/200 999\n")
        (proc / "meminfo").write_text("MemTotal: 16000000 kB\nMemAvailable: 8000000 kB\n"
                                      "SwapTotal: 2000000 kB\nSwapFree: 1500000 kB\n")
        thermal = self.daten / "thermal"
        for nr, wert in enumerate(["44000", "0", "35000"]):
            (thermal / f"thermal_zone{nr}").mkdir(parents=True)
            (thermal / f"thermal_zone{nr}" / "temp").write_text(wert)
        return proc, thermal

    def test_werte(self):
        proc, thermal = self._proc()
        fake = lambda pfad: SimpleNamespace(f_blocks=1000, f_bfree=100, f_bavail=50, f_frsize=1_000_000_000)
        h = self.h.host_info(str(proc), str(thermal), "/", str(self.daten / "gibts-nicht"), fake)
        self.assertEqual(h["uptime_s"], 12345)
        self.assertEqual(h["last"], [0.5, 0.4, 0.3])
        self.assertEqual(h["ram"], {"gesamt_mb": 15625.0, "verfuegbar_mb": 7812.5})
        self.assertEqual(h["swap"], {"gesamt_mb": 1953.1, "frei_mb": 1464.8})
        self.assertEqual(h["temperatur_c"], 44.0)                    # die 0 einer toten Zone zählt nicht
        self.assertFalse(h["neustart_noetig"])
        p = h["platten"][0]
        self.assertEqual((p["pfad"], p["gesamt_gb"], p["belegt_gb"]), ("/", 1000.0, 900.0))
        self.assertEqual(p["prozent"], 94.7)                         # belegt / (belegt + verfügbar), wie df
        self.assertEqual(p["frei_gb"], 50.0)                         # f_bavail: ohne die Reserve für root, wie df

    def test_neustart_noetig(self):
        proc, thermal = self._proc()
        marke = self.daten / "reboot-required"
        marke.write_text("")
        h = self.h.host_info(str(proc), str(thermal), "/", str(marke),
                             lambda p: SimpleNamespace(f_blocks=1, f_bfree=1, f_bavail=1, f_frsize=1))
        self.assertTrue(h["neustart_noetig"])

    def test_fehlendes_proc_gibt_null_statt_absturz(self):
        h = self.h.host_info(str(self.daten / "nix"), str(self.daten / "nix"), "/", "/gibt/es/nicht",
                             mock.Mock(side_effect=OSError))
        self.assertIsNone(h["uptime_s"])
        self.assertIsNone(h["last"])
        self.assertIsNone(h["ram"])
        self.assertIsNone(h["temperatur_c"])
        self.assertEqual(h["platten"], [])


class Docker(Basis):
    def test_container_lesen(self):
        self.befehle({("docker", "ps", "-a", "-q"): (0, "aaa\nbbb\nccc\n", ""),
                      ("docker", "inspect"): (0, json.dumps(INSPECT), "")})
        c = {x["name"]: x for x in self.h.container_lesen()}
        self.assertEqual(sorted(c), ["adguardhome", "jellyfin", "seminar-ki"])
        j = c["jellyfin"]
        self.assertEqual((j["zustand"], j["gesundheit"]), ("running", "healthy"))
        self.assertEqual(j["ports"], [{"host": 8096, "container": 8096}])     # IPv4 und IPv6 nur einmal
        self.assertEqual(j["seit"], 1790071200)
        self.assertEqual(j["neustart_regel"], "unless-stopped")
        self.assertFalse(j["host_netz"])
        self.assertTrue(c["adguardhome"]["host_netz"])
        self.assertIsNone(c["seminar-ki"]["seit"])                            # gestoppt: keine Laufzeit
        self.assertEqual(c["seminar-ki"]["gesundheit"], "")

    def test_docker_aus_ist_none_nicht_leer(self):
        self.befehle({("docker", "ps"): (1, "", "Cannot connect to the Docker daemon")})
        self.assertIsNone(self.h.container_lesen())

    def test_kein_container_ist_leere_liste(self):
        self.befehle({("docker", "ps", "-a", "-q"): (0, "", "")})
        self.assertEqual(self.h.container_lesen(), [])

    def test_kaputte_inspect_ausgabe(self):
        self.befehle({("docker", "ps", "-a", "-q"): (0, "aaa\n", ""), ("docker", "inspect"): (0, "{kaputt", "")})
        self.assertIsNone(self.h.container_lesen())

    def test_stats(self):
        zeilen = "\n".join(json.dumps(d) for d in [
            {"Name": "friday", "CPUPerc": "4.43%", "MemUsage": "136.5MiB / 3GiB"},
            {"Name": "waha", "CPUPerc": "--", "MemUsage": "0B / 0B"}]) + "\nkeinjson\n"
        self.befehle({("docker", "stats"): (0, zeilen, "")})
        s = self.h.stats_lesen()
        self.assertEqual(s["friday"], {"cpu_prozent": 4.43, "ram_mb": 136.5})
        self.assertEqual(s["waha"], {"cpu_prozent": None, "ram_mb": 0.0})

    def test_docker_platz(self):
        zeilen = "\n".join(json.dumps(d) for d in [
            {"Type": "Images", "Reclaimable": "887.6MB (4%)"}, {"Type": "Containers", "Reclaimable": "49.15kB (0%)"},
            {"Type": "Build Cache", "Reclaimable": "1.95GB"}])
        self.befehle({("docker", "system", "df"): (0, zeilen, "")})
        self.assertEqual(self.h.docker_platz_lesen(), {"build_cache_gb": 1.95, "images_frei_gb": 0.89})

    def test_updates(self):
        aus = ("Listing...\nlibfoo/noble-updates 2 amd64 [upgradable from: 1]\n"
               "bar/noble-security 3 amd64 [upgradable from: 2]\n")
        self.befehle({("apt", "list"): (0, aus, "")})
        self.assertEqual(self.h.updates_lesen(), 2)
        self.befehle({("apt", "list"): (1, "", "")})
        self.assertIsNone(self.h.updates_lesen())


class Weiteres(Basis):
    def test_tailnet(self):
        d = {"MagicDNSSuffix": "beispiel.ts.net",
             "Self": {"HostName": "hphome", "DNSName": "hphome.beispiel.ts.net.", "OS": "linux", "Online": True},
             "Peer": {"a": {"HostName": "localhost", "DNSName": "ipad148.beispiel.ts.net.", "OS": "iOS",
                            "Online": False, "LastSeen": "2026-09-25T20:19:29.1Z"},
                      "b": {"HostName": "pc", "DNSName": "pc-beispiel.beispiel.ts.net.", "OS": "windows",
                            "Online": True, "LastSeen": "2026-09-26T10:00:00Z"}}}
        self.befehle({("tailscale", "status", "--json"): (0, json.dumps(d), "")})
        t = self.h.tailnet_lesen()
        self.assertEqual(t["name"], "beispiel.ts.net")
        self.assertEqual([g["name"] for g in t["geraete"]], ["hphome", "pc-beispiel", "ipad148"])   # online zuerst
        self.assertTrue(t["geraete"][0]["selbst"])
        ipad = t["geraete"][2]
        self.assertFalse(ipad["online"])
        self.assertEqual(ipad["zuletzt"], 1790367569)
        self.assertIsNone(t["geraete"][1]["zuletzt"])                 # online: kein „zuletzt“

    def test_tailnet_ohne_tailscale(self):
        self.befehle({("tailscale",): (127, "", "nicht gefunden")})
        self.assertIsNone(self.h.tailnet_lesen())

    def test_sicherung(self):
        self.assertIsNone(self.h.sicherung_lesen(self.daten))
        (self.daten / "sicherung").mkdir()
        (self.daten / "sicherung" / "a.db").write_bytes(b"x" * 1024 * 1024)
        (self.daten / "sicherung" / "b.db").write_bytes(b"x" * 1024 * 1024)
        s = self.h.sicherung_lesen(self.daten)
        self.assertEqual((s["dateien"], s["groesse_mb"]), (2, 2.0))
        self.assertAlmostEqual(s["zuletzt"], time.time(), delta=5)

    def test_sicherung_als_symlink_zaehlt_nicht(self):
        echt = self.daten / "echt"
        echt.mkdir()
        (echt / "a.db").write_bytes(b"x" * 1024)
        (self.daten / "sicherung").symlink_to(echt, target_is_directory=True)
        self.assertIsNone(self.h.sicherung_lesen(self.daten))

    def test_symlinks_in_der_sicherung_zaehlen_nicht(self):
        (self.daten / "sicherung").mkdir()
        (self.daten / "sicherung" / "a.db").write_bytes(b"x" * 1024 * 1024)
        fremd = self.daten / "fremd.db"
        fremd.write_bytes(b"x" * 5 * 1024 * 1024)
        (self.daten / "sicherung" / "b.db").symlink_to(fremd)
        (self.daten / "sicherung" / "ordner").mkdir()
        s = self.h.sicherung_lesen(self.daten)
        self.assertEqual((s["dateien"], s["groesse_mb"]), (1, 1.0))

    def test_speicher(self):
        home = self.daten / "home"
        (home / "media").mkdir(parents=True)
        (home / "downloads").mkdir()
        self.befehle({
            ("du", "-x", "-k", "--max-depth=1", str(home)): (0, f"100\t{home / 'docker'}\n5000000\t{home / 'media'}\n"
                                                                  f"9000000\t{home}\n", ""),
            ("du", "-x", "-k", "--max-depth=1", str(home / "media")): (0, f"3000000\t{home / 'media' / 'serien'}\n"
                                                                          f"2000000\t{home / 'media'}\n", ""),
            ("du", "-x", "-k", "--max-depth=1", str(home / "downloads")): (1, f"4000000\t{home / 'downloads' / 'x'}\n", "Keine Berechtigung"),
        })
        o = self.h.speicher_lesen(home)
        # nach Größe: media 5,12 · downloads/x 4,10 (trotz rc 1) · media/serien 3,07 · docker 0,00
        self.assertEqual([x["pfad"] for x in o], ["media", "downloads/x", "media/serien", "docker"])
        self.assertEqual(o[0]["groesse_gb"], 5.12)                     # KiB → GB (1e9), Wurzelzeile fehlt
        self.assertLessEqual(len(o), 8)


class Sammeln(Basis):
    def setUp(self):
        super().setUp()
        self.h.host_info = lambda *a, **k: {"uptime_s": 5, "platten": []}
        self.befehle({("docker", "ps", "-a", "-q"): (0, "aaa\n", ""),
                      ("docker", "inspect"): (0, json.dumps(INSPECT), ""),
                      ("docker", "stats"): (0, json.dumps({"Name": "jellyfin", "CPUPerc": "1.5%",
                                                            "MemUsage": "100MiB / 1GiB"}), ""),
                      ("docker", "system", "df"): (0, json.dumps({"Type": "Build Cache", "Reclaimable": "1GB"}), ""),
                      ("apt", "list"): (0, "[upgradable from: 1]\n", ""),
                      ("tailscale",): (127, "", ""), ("du",): (0, "", "")})
        self.s = self.h.Sammler()
        self.s.hintergrund = False

    def test_zustand_hat_alle_teile(self):
        z = self.s.zustand(1000.0)
        self.assertTrue(z["helfer"])
        self.assertEqual(z["aktualisiert"], 1000)
        self.assertTrue(z["container_ok"])
        j = next(c for c in z["container"] if c["name"] == "jellyfin")
        self.assertEqual((j["cpu_prozent"], j["ram_mb"]), (1.5, 100.0))
        self.assertFalse(j["absichtlich_gestoppt"])
        self.assertIsNone(z["tailnet"])
        self.assertEqual(z["host"]["updates"], 1)
        self.assertEqual(z["speicher"]["docker"]["build_cache_gb"], 1.0)
        self.assertIsNone(z["wartung_bis"])

    def test_docker_aus_gibt_container_ok_false(self):
        self.befehle({("docker", "ps"): (1, "", "Daemon aus")})
        z = self.s.zustand(1000.0)
        self.assertFalse(z["container_ok"])
        self.assertEqual(z["container"], [])

    def test_langsames_wird_nicht_bei_jeder_runde_geholt(self):
        self.s.zustand(1000.0)
        self.aufrufe.clear()
        self.s.zustand(1010.0)                                          # 10 s später
        befehle = [a[:2] for a in self.aufrufe]
        self.assertNotIn(["apt", "list"], befehle)                     # stündlich
        self.assertNotIn(["docker", "stats"], befehle)                 # alle 30 s
        self.assertNotIn(["docker", "system"], befehle)                # stündlich
        self.s.zustand(1000.0 + 31)
        self.assertIn(["docker", "stats"], [a[:2] for a in self.aufrufe])

    def test_absichtlich_gestoppt_wird_vermerkt_und_beim_laufen_vergessen(self):
        self.h._schreiben(self.h.ordner() / "absichtlich.json", ["seminar-ki", "jellyfin"])
        z = self.s.zustand(1000.0)
        c = {x["name"]: x for x in z["container"]}
        self.assertTrue(c["seminar-ki"]["absichtlich_gestoppt"])
        self.assertFalse(c["jellyfin"]["absichtlich_gestoppt"])        # läuft wieder → nicht mehr „absichtlich“
        self.assertEqual(self.h._json_lesen(self.h.ordner() / "absichtlich.json", None), ["seminar-ki"])

    def test_wartung(self):
        self.h._schreiben(self.h.ordner() / "wartung.json", {"bis": 2000})
        self.assertEqual(self.s.zustand(1000.0)["wartung_bis"], 2000)
        self.assertIsNone(self.s.zustand(3000.0)["wartung_bis"])       # abgelaufen

    def test_vergiftete_wartung_gibt_none(self):
        for inhalt in ('{"bis": 1e999}', '{"bis": -1e999}', '{"bis": "x"}', '{"bis": []}', '{"bis": NaN}'):
            with self.subTest(inhalt=inhalt):
                (self.h.ordner() / "wartung.json").write_text(inhalt)
                self.assertIsNone(self.s.zustand(1000.0)["wartung_bis"])

    def test_hintergrund_blockiert_die_runde_nicht(self):
        import threading
        frei = threading.Event()
        self.h.updates_lesen = lambda: (frei.wait(5), 7)[1]
        s = self.h.Sammler()                                            # hintergrund = True
        t0 = time.monotonic()
        z = s.zustand(1000.0)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertIsNone(z["host"]["updates"])                         # noch nicht da, aber kein Warten
        frei.set()


NAMEN = "jellyfin\nseminar-ki\nfriday\nadguardhome\n"


class Aktionen(Basis):
    def docker_aufrufe(self):
        return [a for a in self.aufrufe if a[:2] not in (["docker", "ps"],)]

    def test_neustart_stopp_start_rufen_genau_diese_kommandos(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, ""), ("docker", "restart"): (0, "jellyfin", ""),
                      ("docker", "stop"): (0, "", ""), ("docker", "start"): (0, "", "")})
        self.assertTrue(self.h.container_aktion("neustart", "jellyfin")[0])
        self.assertTrue(self.h.container_aktion("stopp", "jellyfin")[0])
        self.assertTrue(self.h.container_aktion("start", "jellyfin")[0])
        self.assertEqual(self.docker_aufrufe(), [["docker", "restart", "jellyfin"], ["docker", "stop", "jellyfin"],
                                                 ["docker", "start", "jellyfin"]])

    def test_boese_und_falsche_namen_erreichen_docker_nie(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, "")})
        for name in ["friday; rm -rf /", "--all", "../x", "", "a" * 200, "Jellyfin", "jelly fin", "$(id)", "jellyfin\nfriday",
                     "jellyfin\n", "nichtda", "-f"]:
            with self.subTest(name=name):
                ok, text = self.h.container_aktion("neustart", name)
                self.assertFalse(ok)
                ok, text = self.h.logs_holen(name)
                self.assertFalse(ok)
        self.assertEqual(self.docker_aufrufe(), [])                    # kein restart, stop, logs …

    def test_unbekannte_aktion(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, "")})
        ok, text = self.h.container_aktion("löschen", "jellyfin")
        self.assertFalse(ok)
        self.assertEqual(self.docker_aufrufe(), [])

    def test_friday_ist_nicht_stoppbar_aber_neu_startbar(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, ""), ("docker", "restart"): (0, "", "")})
        ok, text = self.h.container_aktion("stopp", "friday")
        self.assertFalse(ok)
        self.assertIn("nicht stoppen", text)
        self.assertTrue(self.h.container_aktion("neustart", "friday")[0])

    def test_docker_fehler_wird_gemeldet(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, ""),
                      ("docker", "restart"): (1, "", "Error response from daemon: boom")})
        ok, text = self.h.container_aktion("neustart", "jellyfin")
        self.assertFalse(ok)
        self.assertIn("boom", text)

    def test_docker_aus_bei_der_namenspruefung(self):
        self.befehle({("docker", "ps"): (1, "", "Daemon")})
        ok, text = self.h.container_aktion("neustart", "jellyfin")
        self.assertFalse(ok)
        self.assertIn("Docker", text)

    def test_stopp_merkt_absichtlich_start_vergisst(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, ""), ("docker", "stop"): (0, "", ""),
                      ("docker", "start"): (0, "", "")})
        self.h.container_aktion("stopp", "jellyfin")
        self.assertEqual(self.h._json_lesen(self.h.ordner() / "absichtlich.json", None), ["jellyfin"])
        self.h.container_aktion("start", "jellyfin")
        self.assertEqual(self.h._json_lesen(self.h.ordner() / "absichtlich.json", None), [])

    def test_logs(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, ""),
                      ("docker", "logs"): (0, "zeile eins\n", "zeile zwei (stderr)\n")})
        ok, text = self.h.logs_holen("jellyfin")
        self.assertTrue(ok)
        self.assertIn("zeile eins", text)
        self.assertIn("zeile zwei", text)
        self.assertIn(["docker", "logs", "--tail", "100", "jellyfin"], self.aufrufe)

    def test_lange_logs_werden_gekuerzt_auf_das_ende(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, ""),
                      ("docker", "logs"): (0, "A" * 50000 + "ENDE", "")})
        ok, text = self.h.logs_holen("jellyfin")
        self.assertEqual(len(text), 20000)
        self.assertTrue(text.endswith("ENDE"))

    def test_leere_logs(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, ""), ("docker", "logs"): (0, "", "")})
        self.assertEqual(self.h.logs_holen("jellyfin"), (True, "(keine Zeilen)"))


class BotModell(Basis):
    """Den Minecraft-Bot auf Gemini oder DeepSeek umstellen — Profil, Schlüssel, Neustart.

    Der Helfer schreibt nur in den Mindcraft-Ordner, der Wert ist eine Positivliste, und der Schlüssel kommt
    aus Eikes Datei auf dem Host (nie aus dem Auftrag, nie in der Antwort)."""

    BOT = "friday-mc-mindcraft-1"

    def setUp(self):
        super().setUp()
        self.mc = self.daten / "mindcraft"
        (self.mc / "profiles").mkdir(parents=True)
        (self.mc / "profiles" / "friday.json").write_text(json.dumps({
            "name": "Friday", "model": "gemini-3.1-flash-lite", "code_model": "gemini-3.1-flash-lite",
            "cooldown": 8000, "conversing": "Hallo $NAME", "modes": {"unstuck": False}}), encoding="utf-8")
        (self.mc / "keys.json").write_text(json.dumps({"GEMINI_API_KEY": "g-1", "DEEPSEEK_API_KEY": ""}),
                                           encoding="utf-8")
        self.keydatei = self.daten / "deepseek.key"
        self.keydatei.write_text("sk-geheim-123\n", encoding="utf-8")
        p = mock.patch.dict(os.environ, {"MINDCRAFT_ORDNER": str(self.mc),
                                         "DEEPSEEK_KEY_DATEI": str(self.keydatei)})
        p.start()
        self.addCleanup(p.stop)
        self.befehle({("docker", "ps", "-a", "--format"): (0, self.BOT + "\n", ""),
                      ("docker", "restart"): (0, self.BOT, "")})

    def profil(self):
        return json.loads((self.mc / "profiles" / "friday.json").read_text(encoding="utf-8"))

    def keys(self):
        return json.loads((self.mc / "keys.json").read_text(encoding="utf-8"))

    def docker_aufrufe(self):
        return [a for a in self.aufrufe if a[:2] not in (["docker", "ps"],)]

    def test_deepseek_schreibt_modell_und_schluessel_und_startet_neu(self):
        ok, text = self.h.bot_modell("deepseek")
        self.assertTrue(ok, text)
        self.assertIn("DeepSeek", text)
        p = self.profil()
        self.assertEqual((p["model"], p["code_model"]), ("deepseek-flash", "deepseek-flash"))
        self.assertEqual(p["conversing"], "Hallo $NAME")               # der Rest des Profils bleibt
        self.assertEqual(p["cooldown"], 8000)
        self.assertEqual(self.keys()["DEEPSEEK_API_KEY"], "sk-geheim-123")
        self.assertEqual(self.keys()["GEMINI_API_KEY"], "g-1")         # andere Schlüssel bleiben
        self.assertEqual((self.mc / "keys.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.docker_aufrufe(), [["docker", "restart", self.BOT]])
        self.assertEqual(self.h.mindcraft_modell(), "deepseek")
        self.assertNotIn("sk-geheim-123", text)                        # der Schlüssel steht nie in der Antwort

    def test_gemini_zurueck(self):
        self.assertTrue(self.h.bot_modell("deepseek")[0])
        ok, text = self.h.bot_modell("gemini")
        self.assertTrue(ok, text)
        self.assertEqual(self.profil()["model"], "gemini-3.1-flash-lite")
        self.assertEqual(self.profil()["code_model"], "gemini-3.1-flash-lite")
        self.assertEqual(self.h.mindcraft_modell(), "gemini")
        self.assertEqual(self.docker_aufrufe(), [["docker", "restart", self.BOT],
                                                 ["docker", "restart", self.BOT]])

    def test_unbekanntes_modell_aendert_nichts(self):
        for wert in ("gpt-4", "claude", "", "deepseek; rm -rf /", "../../etc"):
            with self.subTest(wert=wert):
                ok, text = self.h.bot_modell(wert)
                self.assertFalse(ok)
                self.assertEqual(self.profil()["model"], "gemini-3.1-flash-lite")
        self.assertEqual(self.docker_aufrufe(), [])

    def test_ohne_schluessel_kein_deepseek(self):
        self.keydatei.write_text("", encoding="utf-8")
        ok, text = self.h.bot_modell("deepseek")
        self.assertFalse(ok)
        self.assertIn("Schlüssel", text)
        self.assertEqual(self.profil()["model"], "gemini-3.1-flash-lite")   # nichts geändert
        self.assertEqual(self.docker_aufrufe(), [])

    def test_kaputter_schluessel_zaehlt_nicht(self):
        self.keydatei.write_text("nicht-von-deepseek", encoding="utf-8")
        self.assertFalse(self.h.bot_modell("deepseek")[0])

    def test_fehlendes_profil_oder_keys(self):
        (self.mc / "profiles" / "friday.json").unlink()
        self.assertFalse(self.h.bot_modell("deepseek")[0])
        (self.mc / "profiles" / "friday.json").write_text('{"name": "Friday"}', encoding="utf-8")
        (self.mc / "keys.json").unlink()
        self.assertFalse(self.h.bot_modell("deepseek")[0])
        self.assertEqual(self.docker_aufrufe(), [])

    def test_symlink_wird_nicht_befolgt(self):
        ziel = self.daten / "aussen.json"
        ziel.write_text('{"name": "Aussen"}', encoding="utf-8")
        (self.mc / "profiles" / "friday.json").unlink()
        (self.mc / "profiles" / "friday.json").symlink_to(ziel)
        ok, text = self.h.bot_modell("deepseek")
        self.assertFalse(ok)                                           # ein Link ist kein Profil
        self.assertEqual(ziel.read_text(encoding="utf-8"), '{"name": "Aussen"}')

    def test_auftrag_mit_wert_und_verlauf_ohne_schluessel(self):
        antwort, _ = self.h.auftrag_ausfuehren({"id": "1", "aktion": "bot_modell", "wert": "deepseek"})
        self.assertTrue(antwort["ok"], antwort["text"])
        verlauf = (self.h.ordner() / "verlauf.jsonl").read_text(encoding="utf-8")
        self.assertIn("bot_modell", verlauf)
        self.assertNotIn("sk-geheim-123", verlauf)

    def test_zustand_nennt_das_modell(self):
        self.h.bot_modell("deepseek")
        self.assertEqual(self.h.mindcraft_modell(), "deepseek")
        (self.mc / "profiles" / "friday.json").write_text('{"model": "etwas-anderes"}', encoding="utf-8")
        self.assertEqual(self.h.mindcraft_modell(), "")


class HostNeustart(Basis):
    def test_ohne_sudo_regel_wird_nichts_ausgefuehrt(self):
        self.befehle({("sudo", "-n", "-l"): (1, "", "a password is required")})
        ok, text, danach = self.h.host_neustart()
        self.assertFalse(ok)
        self.assertFalse(danach)
        self.assertIn("/etc/sudoers.d/friday-hphome", text)
        self.assertIn("NOPASSWD: /usr/bin/systemctl reboot", text)
        self.assertFalse((self.h.ordner() / "wartung.json").exists())     # keine Wartung gesetzt
        self.assertEqual([a for a in self.aufrufe if "reboot" in a and "-l" not in a], [])

    def test_mit_regel_wartung_gesetzt_und_danach_gemeldet(self):
        self.befehle({("sudo", "-n", "-l"): (0, "/usr/bin/systemctl reboot", "")})
        vorher = time.time()
        ok, text, danach = self.h.host_neustart()
        self.assertTrue(ok)
        self.assertTrue(danach)
        bis = self.h._json_lesen(self.h.ordner() / "wartung.json", {})["bis"]
        self.assertAlmostEqual(bis, vorher + 600, delta=5)
        # geprüft wurde nur, ausgeführt (noch) nicht:
        self.assertEqual(self.aufrufe, [["sudo", "-n", "-l", "/usr/bin/systemctl", "reboot"]])


class PlatzSchaffen(Basis):
    def test_genau_zwei_kommandos_mit_24_stunden_filter(self):
        self.befehle({("docker", "builder", "prune"): (0, "Deleted build cache objects:\nabc\nTotal:  1.95GB\n", ""),
                      ("docker", "image", "prune"): (0, "Deleted Images:\nx\nTotal reclaimed space: 887.6MB\n", "")})
        ok, text = self.h.platz_schaffen()
        self.assertTrue(ok)
        self.assertIn("2.84 GB", text)
        self.assertEqual(self.aufrufe, [["docker", "builder", "prune", "-f", "--filter", "until=24h"],
                                        ["docker", "image", "prune", "-a", "-f", "--filter", "until=24h"]])
        for a in self.aufrufe:
            self.assertNotIn("volume", a)
            self.assertNotIn("system", a)

    def test_fehler_bricht_ab(self):
        self.befehle({("docker", "builder", "prune"): (1, "", "kaputt")})
        ok, text = self.h.platz_schaffen()
        self.assertFalse(ok)
        self.assertEqual(len(self.aufrufe), 1)                            # das zweite Kommando läuft nicht mehr


class Auftraege(Basis):
    def test_unbekannte_aktion_und_boese_felder(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, "")})
        for auftrag in [{"id": "1", "aktion": "exec", "name": "jellyfin"}, {"id": "2", "aktion": "rm -rf /"},
                        {"id": "3"}, {"id": "4", "aktion": "neustart", "name": "friday; reboot"},
                        {"id": "5", "aktion": ["neustart"], "name": ["jellyfin"]}]:
            with self.subTest(auftrag=auftrag):
                antwort, danach = self.h.auftrag_ausfuehren(auftrag)
                self.assertFalse(antwort["ok"])
                self.assertFalse(danach)
        self.assertEqual([a for a in self.aufrufe if a[:2] != ["docker", "ps"]], [])

    def test_antwort_traegt_die_id_gekuerzt(self):
        antwort, _ = self.h.auftrag_ausfuehren({"id": "x" * 500, "aktion": "gibtsnicht"})
        self.assertEqual(len(antwort["id"]), 64)

    def test_verlauf_ohne_logs(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, ""), ("docker", "restart"): (0, "", ""),
                      ("docker", "logs"): (0, "geheim", "")})
        self.h.auftrag_ausfuehren({"id": "a", "aktion": "neustart", "name": "jellyfin"})
        self.h.auftrag_ausfuehren({"id": "b", "aktion": "logs", "name": "jellyfin"})
        zeilen = [json.loads(z) for z in (self.h.ordner() / "verlauf.jsonl").read_text().splitlines()]
        self.assertEqual([(z["aktion"], z["name"], z["ok"]) for z in zeilen], [("neustart", "jellyfin", True)])
        self.assertNotIn("geheim", (self.h.ordner() / "verlauf.jsonl").read_text())   # Logs stehen nie im Verlauf

    def test_abstand_zwischen_aktionen(self):
        self.h.MIN_ABSTAND_S = 60
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, ""), ("docker", "restart"): (0, "", "")})
        a1, _ = self.h.auftrag_ausfuehren({"id": "1", "aktion": "neustart", "name": "jellyfin"})
        a2, _ = self.h.auftrag_ausfuehren({"id": "2", "aktion": "neustart", "name": "jellyfin"})
        self.assertTrue(a1["ok"])
        self.assertFalse(a2["ok"])
        self.assertIn("Moment", a2["text"])

    def test_handy_start_ruft_nur_das_feste_skript(self):
        aufrufe = []
        def fake(befehl, zeitlimit=20):
            aufrufe.append(befehl)
            return 0, "An", ""
        with mock.patch.object(self.h, "ausfuehren", fake):
            antwort, _ = self.h.auftrag_ausfuehren({"id": "1", "aktion": "handy_start", "name": "$(id)"})
        self.assertTrue(antwort["ok"])
        self.assertEqual(len(aufrufe), 1)
        self.assertEqual(aufrufe[0][0], "bash")
        self.assertTrue(aufrufe[0][1].endswith("handy_steuerung_starten.sh"))
        self.assertEqual(len(aufrufe[0]), 2)

    def test_platz_schaffen_setzt_den_docker_zaehler_zurueck(self):
        self.befehle({("docker", "builder", "prune"): (0, "Total: 1GB", ""), ("docker", "image", "prune"): (0, "", "")})
        s = mock.Mock()
        self.h.auftrag_ausfuehren({"id": "1", "aktion": "platz_schaffen"}, s)
        s.vergessen.assert_called_once_with("docker")


    def test_zu_langer_name_wird_nicht_gekuerzt_sondern_abgelehnt(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, "a" * 63 + "\n", "")})
        for name in ["a" * 64, "a" * 200]:
            with self.subTest(laenge=len(name)):
                antwort, _ = self.h.auftrag_ausfuehren({"id": "n", "aktion": "neustart", "name": name})
                self.assertFalse(antwort["ok"])
                self.assertIn("Ungültiger Containername", antwort["text"])
        self.assertEqual(self.aufrufe, [])                                 # gar kein docker-Aufruf

    def test_antwort_id_wird_auf_sichere_zeichen_beschraenkt(self):
        antwort, _ = self.h.auftrag_ausfuehren({"id": "a/b ../c;\n$(x)-_9", "aktion": "gibtsnicht"})
        self.assertEqual(antwort["id"], "abcx-_9")

    def test_verlauf_traegt_nur_geprueften_namen(self):
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, "")})
        self.h.auftrag_ausfuehren({"id": "1", "aktion": "neustart", "name": "friday; reboot"})
        zeilen = [json.loads(z) for z in (self.h.ordner() / "verlauf.jsonl").read_text().splitlines()]
        self.assertEqual(len(zeilen), 1)
        self.assertEqual(zeilen[0]["name"], "")
        self.assertNotIn("reboot", (self.h.ordner() / "verlauf.jsonl").read_text())

    def _opfer(self):
        opfer = self.daten / "opfer.txt"
        opfer.write_text("OPFER")
        opfer.chmod(0o644)
        return opfer

    def _unveraendert(self, opfer):
        self.assertEqual(opfer.read_text(), "OPFER")
        self.assertEqual(oct(opfer.stat().st_mode & 0o777), "0o644")

    def test_verlauf_folgt_keinem_symlink(self):
        opfer = self._opfer()
        (self.h.ordner() / "verlauf.jsonl").symlink_to(opfer)
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, ""), ("docker", "restart"): (0, "", "")})
        antwort, _ = self.h.auftrag_ausfuehren({"id": "1", "aktion": "neustart", "name": "jellyfin"})
        self.assertTrue(antwort["ok"])                                     # die Aktion selbst geht durch
        self._unveraendert(opfer)

    def test_schreiben_folgt_keinem_symlink_auf_tmp(self):
        opfer = self._opfer()
        o = self.h.ordner()
        (o / "absichtlich.tmp").symlink_to(opfer)
        self.befehle({("docker", "ps", "-a", "--format"): (0, NAMEN, ""), ("docker", "stop"): (0, "", "")})
        self.assertTrue(self.h.container_aktion("stopp", "jellyfin")[0])
        self._unveraendert(opfer)
        self.assertEqual(self.h._json_lesen(o / "absichtlich.json", None), ["jellyfin"])
        self.assertFalse((o / "absichtlich.json").is_symlink())

    def test_schreiben_ersetzt_einen_symlink_am_ziel(self):
        opfer = self._opfer()
        o = self.h.ordner()
        (o / "wartung.json").symlink_to(opfer)
        self.h._schreiben(o / "wartung.json", {"bis": 1})
        self._unveraendert(opfer)
        self.assertEqual(self.h._json_lesen(o / "wartung.json", None), {"bis": 1})

    def test_verlauf_wird_gekuerzt_ohne_symlink_zu_folgen(self):
        o = self.h.ordner()
        (o / "verlauf.jsonl").write_text("\n".join(json.dumps({"n": i}) for i in range(1001)) + "\n")
        self.h._verlauf("neustart", "jellyfin", True, "ok")
        zeilen = (o / "verlauf.jsonl").read_text().splitlines()
        self.assertLessEqual(len(zeilen), 501)
        self.assertEqual(json.loads(zeilen[-1])["aktion"], "neustart")
        self.assertEqual(oct((o / "verlauf.jsonl").stat().st_mode & 0o777), "0o600")

    def test_ordner_lehnt_symlink_ab(self):
        ziel = self.daten / "woanders"
        ziel.mkdir()
        (self.daten / "hphome").symlink_to(ziel, target_is_directory=True)
        with self.assertRaises(OSError):
            self.h.ordner()


class SchleifenBasis(Basis):
    def setUp(self):
        super().setUp()
        self.h.host_info = lambda *a, **k: {"uptime_s": 5, "platten": []}
        self.s = self.h.Sammler()
        self.s.hintergrund = False
        self.befehle({("docker", "ps", "-a", "-q"): (0, "", ""), ("docker", "ps", "-a", "--format"): (0, NAMEN, ""),
                      ("docker", "restart"): (0, "", ""), ("tailscale",): (127, "", ""), ("du",): (0, "", ""),
                      ("docker", "stats"): (0, "", ""), ("docker", "system"): (0, "", ""), ("apt",): (127, "", "")})
        p = mock.patch("time.sleep")                                       # die Runden sollen nicht wirklich warten
        p.start()
        self.addCleanup(p.stop)


class Schleife(SchleifenBasis):
    def test_zustand_wird_geschrieben_und_auftrag_abgearbeitet(self):
        o = self.h.ordner()
        (o / "auftrag.json").write_text(json.dumps({"id": "abc", "aktion": "neustart", "name": "jellyfin"}))
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        z = json.loads((o / "zustand.json").read_text())
        self.assertTrue(z["helfer"])
        self.assertEqual(z["letzte_antwort"]["id"], "abc")
        self.assertTrue(z["letzte_antwort"]["ok"])
        self.assertFalse((o / "auftrag.json").exists())                    # abgeholt
        self.assertEqual(oct((o / "zustand.json").stat().st_mode & 0o777), "0o600")

    def test_kaputter_auftrag_wird_verworfen(self):
        o = self.h.ordner()
        (o / "auftrag.json").write_text("{kaputt")
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        self.assertFalse((o / "auftrag.json").exists())
        self.assertTrue((o / "zustand.json").exists())

    def test_letzte_antwort_ueberlebt_einen_neustart_des_helfers(self):
        o = self.h.ordner()
        (o / "zustand.json").write_text(json.dumps({"letzte_antwort": {"id": "alt", "ok": True, "text": "ok"}}))
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        self.assertEqual(json.loads((o / "zustand.json").read_text())["letzte_antwort"]["id"], "alt")

    def test_host_neustart_erst_nach_der_antwort(self):
        """Der Neustart selbst läuft erst, nachdem die Antwort in der Zustandsdatei steht — sonst bekäme die App nie eine."""
        o = self.h.ordner()
        gesehen = []
        vorher = self.h.ausfuehren

        def mit_sudo(befehl, zeitlimit=20):
            if befehl == ["sudo", "-n", "-l", "/usr/bin/systemctl", "reboot"]:
                return 0, "", ""
            if befehl == ["sudo", "-n", "/usr/bin/systemctl", "reboot"]:
                gesehen.append(json.loads((o / "zustand.json").read_text())["letzte_antwort"]["text"])
                return 0, "", ""
            return vorher(befehl, zeitlimit)
        self.h.ausfuehren = mit_sudo
        (o / "auftrag.json").write_text(json.dumps({"id": "r1", "aktion": "host_neustart"}))
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        self.assertEqual(len(gesehen), 1)                                  # der Neustart lief genau einmal …
        self.assertIn("startet neu", gesehen[0])                           # … und die Antwort stand da schon in der Datei

    def test_ein_fehler_stoppt_den_helfer_nicht(self):
        self.h.host_info = mock.Mock(side_effect=RuntimeError("boom"))
        (self.h.ordner() / "auftrag.json").write_text(json.dumps({"id": "z", "aktion": "gibtsnicht"}))
        self.h.lauf(takt=0, runden=3, sammler=self.s)                      # wirft nicht


    def _nach_runde_ok(self, o):
        self.assertFalse((o / "auftrag.json").exists())
        self.assertFalse((o / "auftrag.arbeit").exists())
        z = json.loads((o / "zustand.json").read_text())                   # trotzdem geschrieben
        self.assertTrue(z["helfer"])
        return z

    def test_tief_verschachtelter_auftrag_blockiert_den_helfer_nicht(self):
        o = self.h.ordner()
        for tiefe in (100000, 2000):                                       # zu groß, und klein genug für RecursionError
            with self.subTest(tiefe=tiefe):
                (o / "auftrag.json").write_text("[" * tiefe)
                self.h.lauf(takt=0, runden=2, sammler=self.s)
                self._nach_runde_ok(o)

    def test_zu_grosser_auftrag_wird_verworfen(self):
        o = self.h.ordner()
        (o / "auftrag.json").write_text(json.dumps({"id": "g", "aktion": "gibtsnicht", "x": "a" * 5000}))
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        z = self._nach_runde_ok(o)
        self.assertFalse(z["letzte_antwort"])                              # nie ausgeführt

    def test_auftrag_kein_dict_oder_kein_utf8_wird_verworfen(self):
        o = self.h.ordner()
        for inhalt in [b"[]", b'"x"', b"5", b"\xff\xfe{}"]:
            with self.subTest(inhalt=inhalt):
                (o / "auftrag.json").write_bytes(inhalt)
                self.h.lauf(takt=0, runden=1, sammler=self.s)
                z = self._nach_runde_ok(o)
                self.assertFalse(z["letzte_antwort"])

    def test_auftrag_als_symlink_wird_nicht_gelesen(self):
        o = self.h.ordner()
        opfer = self.daten / "opfer.txt"
        opfer.write_text(json.dumps({"id": "s", "aktion": "gibtsnicht"}))
        (o / "auftrag.json").symlink_to(opfer)
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        z = self._nach_runde_ok(o)
        self.assertFalse(z["letzte_antwort"])
        self.assertTrue(opfer.exists())                                    # das Ziel bleibt unangetastet

    def test_zustand_tmp_symlink_wird_nicht_befolgt(self):
        o = self.h.ordner()
        opfer = self.daten / "opfer.txt"
        opfer.write_text("OPFER")
        opfer.chmod(0o644)
        (o / "zustand.tmp").symlink_to(opfer)
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        self.assertEqual(opfer.read_text(), "OPFER")
        self.assertEqual(oct(opfer.stat().st_mode & 0o777), "0o644")
        self.assertTrue(json.loads((o / "zustand.json").read_text())["helfer"])

    def test_kaputte_zustandsdatei_beim_start_stoppt_nicht(self):
        o = self.h.ordner()
        (o / "zustand.json").write_text("[1, 2]")
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        self.assertTrue(json.loads((o / "zustand.json").read_text())["helfer"])

    def test_kaputte_absichtlich_datei_stoppt_den_zustand_nicht(self):
        o = self.h.ordner()
        (o / "absichtlich.json").write_text("5")
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        self.assertTrue(json.loads((o / "zustand.json").read_text())["helfer"])


    def _mit_zeitlimit(self, fn, sekunden=10):
        """Läuft `fn` in einem Thread: hängt der Helfer (z. B. an einem FIFO), scheitert der Test, statt die Suite anzuhalten."""
        import threading
        fehler = []

        def ziel():
            try:
                fn()
            except BaseException as exc:                                  # noqa: BLE001
                fehler.append(exc)
        t = threading.Thread(target=ziel, daemon=True)
        t.start()
        t.join(sekunden)
        self.assertFalse(t.is_alive(), "der Helfer hängt")
        if fehler:
            raise fehler[0]

    def test_ordner_als_arbeitsdatei_haelt_den_helfer_nicht_fest(self):
        o = self.h.ordner()
        (o / "auftrag.arbeit").mkdir()
        (o / "auftrag.arbeit" / "x").write_text("y")
        (o / "auftrag.json").write_text(json.dumps({"id": "d1", "aktion": "neustart", "name": "jellyfin"}))
        self.h.lauf(takt=0, runden=2, sammler=self.s)
        z = self._nach_runde_ok(o)
        self.assertEqual(z["letzte_antwort"]["id"], "d1")                  # der Auftrag lief trotzdem
        self.assertFalse((o / "auftrag.arbeit").exists())

    def test_ordner_als_auftragsdatei_wird_verworfen(self):
        o = self.h.ordner()
        (o / "auftrag.json").mkdir()
        (o / "auftrag.json" / "x").write_text("y")
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        self._nach_runde_ok(o)
        self.assertFalse((o / "auftrag.json").exists())
        (o / "auftrag.json").write_text(json.dumps({"id": "n2", "aktion": "neustart", "name": "jellyfin"}))
        self.h.lauf(takt=0, runden=1, sammler=self.s)                      # die nächste Runde arbeitet wieder
        self.assertEqual(self._nach_runde_ok(o)["letzte_antwort"]["id"], "n2")

    def test_ordner_als_zustandsdatei_oder_tmp(self):
        o = self.h.ordner()
        for name in ("zustand.tmp", "zustand.json"):
            with self.subTest(name=name):
                (o / "zustand.json").unlink(missing_ok=True)
                (o / name).mkdir()
                (o / name / "x").write_text("y")
                self.h._schreiben(o / "zustand.json", {"a": 1})
                self.assertTrue((o / "zustand.json").is_file())
                self.assertFalse((o / "zustand.json").is_symlink())
                self.assertEqual(json.loads((o / "zustand.json").read_text()), {"a": 1})

    def test_ordner_als_zustandsdatei_in_der_schleife(self):
        o = self.h.ordner()
        (o / "zustand.json").mkdir()
        (o / "zustand.tmp").mkdir()
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        self.assertTrue(json.loads((o / "zustand.json").read_text())["helfer"])

    def test_fifo_als_auftragsdatei_haelt_den_helfer_nicht_fest(self):
        o = self.h.ordner()
        os.mkfifo(o / "auftrag.json")
        self._mit_zeitlimit(lambda: self.h.lauf(takt=0, runden=1, sammler=self.s))
        self._nach_runde_ok(o)

    def test_fifo_als_zustandsdatei_wird_wie_leer_behandelt(self):
        o = self.h.ordner()
        for name in ("absichtlich.json", "wartung.json"):
            os.mkfifo(o / name)
        z = {}
        self._mit_zeitlimit(lambda: z.update(self.s.zustand(1000.0)))
        self.assertIn("container", z)                                      # die Runde lief durch
        self.assertIsNone(z["wartung_bis"])
        os.mkfifo(o / "zustand.json")
        self._mit_zeitlimit(lambda: self.h.lauf(takt=0, runden=1, sammler=self.s))    # liest beim Start zustand.json
        self.assertTrue(json.loads((o / "zustand.json").read_text())["helfer"])       # und ersetzt das FIFO

    def test_json_lesen_liest_nur_gewoehnliche_dateien_begrenzt(self):
        o = self.h.ordner()
        os.mkfifo(o / "f.json")
        self._mit_zeitlimit(lambda: self.assertEqual(self.h._json_lesen(o / "f.json", "std"), "std"))
        (o / "gross.json").write_text('"' + "a" * 3000 + '"')
        self.assertEqual(self.h._json_lesen(o / "gross.json", "std", 1000), "std")
        (o / "kein_utf8.json").write_bytes(b"\xff\xfe")
        self.assertEqual(self.h._json_lesen(o / "kein_utf8.json", "std"), "std")
        (o / "tief.json").write_text("[" * 5000)
        self.assertEqual(self.h._json_lesen(o / "tief.json", "std"), "std")

    def test_verlauf_als_fifo_haelt_nicht_fest(self):
        o = self.h.ordner()
        os.mkfifo(o / "verlauf.jsonl")
        self._mit_zeitlimit(lambda: self.h._verlauf("neustart", "jellyfin", True, "ok"))

    def test_wartung_falscher_typ(self):
        for inhalt in ("[1]", '"x"', "5", "null"):
            with self.subTest(inhalt=inhalt):
                (self.h.ordner() / "wartung.json").write_text(inhalt)
                self.assertIsNone(self.s.zustand(1000.0)["wartung_bis"])


    def _sperren(self, ordner_, modus):
        """Legt einen Ordner mit Inhalt und entzogenen Rechten an; die Rechte kommen beim Aufräumen zurück."""
        ordner_.mkdir()
        (ordner_ / "x").write_text("y")
        ordner_.chmod(modus)

        def zurueck():
            for pfad in [ordner_, *[p for p in self.daten.rglob(".weg-*")]]:
                try:
                    pfad.chmod(0o700)
                except OSError:
                    pass
        self.addCleanup(zurueck)

    def test_zustand_als_gesperrter_ordner_wird_ersetzt(self):
        o = self.h.ordner()
        for modus in (0o000, 0o500):
            with self.subTest(modus=oct(modus)):
                (o / "zustand.json").unlink(missing_ok=True)
                self._sperren(o / "zustand.json", modus)
                self.h._schreiben(o / "zustand.json", {"a": modus})
                self.assertTrue((o / "zustand.json").is_file())
                self.assertEqual(json.loads((o / "zustand.json").read_text()), {"a": modus})
                self.assertEqual([p for p in o.iterdir() if p.name.endswith(".tmp")], [])   # kein Zwischenrest

    def test_gesperrte_arbeitsdatei_haelt_die_auftraege_nicht_auf(self):
        o = self.h.ordner()
        self._sperren(o / "auftrag.arbeit", 0o500)
        (o / "auftrag.json").write_text(json.dumps({"id": "g1", "aktion": "neustart", "name": "jellyfin"}))
        self.h.lauf(takt=0, runden=2, sammler=self.s)
        z = self._nach_runde_ok(o)
        self.assertEqual(z["letzte_antwort"]["id"], "g1")
        self.assertFalse((o / "auftrag.arbeit").exists())

    def test_feste_tmp_namen_bringen_nichts_mehr(self):
        o = self.h.ordner()
        (o / "zustand.tmp").mkdir()
        (o / "zustand.json.tmp").write_text("besetzt")
        (o / "absichtlich.tmp").write_text("besetzt")
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        self.assertTrue(json.loads((o / "zustand.json").read_text())["helfer"])
        self.assertEqual((o / "zustand.json.tmp").read_text(), "besetzt")   # wird gar nicht mehr benutzt

    def test_tmp_wird_bei_fehler_wieder_entfernt(self):
        o = self.h.ordner()
        with mock.patch("os.replace", side_effect=OSError("kaputt")):
            with self.assertRaises(OSError):
                self.h._schreiben(o / "zustand.json", {"a": 1})
        self.assertEqual([p.name for p in o.iterdir() if p.name.endswith(".tmp")], [])

    def test_liegengebliebenes_wird_aufgeraeumt(self):
        o = self.h.ordner()
        self._sperren(o / ".weg-abc", 0o500)
        alt, neu = o / "zustand.json.aaaa.tmp", o / "zustand.json.bbbb.tmp"
        alt.write_text("x")
        neu.write_text("x")
        os.utime(alt, (time.time() - 3600, time.time() - 3600))
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        self.assertFalse((o / ".weg-abc").exists())
        self.assertFalse(alt.exists())
        self.assertTrue(neu.exists())                                      # frisch: könnte gerade in Arbeit sein

    def test_symlink_auf_ordner_wird_entfernt_das_ziel_bleibt(self):
        o = self.h.ordner()
        ziel = self.daten / "ziel"
        ziel.mkdir()
        (ziel / "wichtig.txt").write_text("bleibt")
        (o / "auftrag.arbeit").symlink_to(ziel, target_is_directory=True)
        (o / "zustand.json").symlink_to(ziel, target_is_directory=True)
        (o / "auftrag.json").write_text(json.dumps({"id": "l1", "aktion": "neustart", "name": "jellyfin"}))
        self.h.lauf(takt=0, runden=2, sammler=self.s)
        z = self._nach_runde_ok(o)
        self.assertEqual(z["letzte_antwort"]["id"], "l1")
        self.assertFalse((o / "zustand.json").is_symlink())
        self.assertFalse((o / "auftrag.arbeit").exists())
        self.assertEqual((ziel / "wichtig.txt").read_text(), "bleibt")

    def test_riesiger_verlauf_wird_neu_begonnen(self):
        o = self.h.ordner()
        with open(o / "verlauf.jsonl", "wb") as f:
            f.truncate(self.h.ZUSTAND_MAX + 1000)                          # sparse, über der Grenze
        self.h._verlauf("neustart", "jellyfin", True, "ok")
        zeilen = (o / "verlauf.jsonl").read_text().splitlines()
        self.assertEqual(len(zeilen), 1)
        self.assertEqual(json.loads(zeilen[0])["aktion"], "neustart")
        self.assertEqual(oct((o / "verlauf.jsonl").stat().st_mode & 0o777), "0o600")

    # ------------------------------------------------ tiefe Bäume, Symlink-Tausch (Fix-Runde 4)

    def _baeume_spaeter_weg(self, o):
        def weg():
            for e in list(os.scandir(o)):
                if e.name.startswith(".weg-") or e.name in ("zustand.json", "auftrag.arbeit"):
                    baum_weg(Path(e.path))
        self.addCleanup(weg)

    def _zustand_schreibzaehler(self):
        echt, zahl = self.h._schreiben, []

        def zaehlen(pfad, daten):
            echt(pfad, daten)
            if pfad.name == "zustand.json":
                zahl.append(1)
        self.h._schreiben = zaehlen
        return zahl

    def test_tiefer_baum_blockiert_keine_runde(self):
        """Ein Baum tiefer als die Rekursionsgrenze (und als der fd-Vorrat) als `zustand.json` oder als altes `.weg-*`:
        jede Runde schreibt trotzdem den Zustand, nichts wirft, kein fd bleibt offen."""
        zahl = self._zustand_schreibzaehler()
        o = self.h.ordner()
        self._baeume_spaeter_weg(o)
        for tiefe in (1200, 1500):                                     # Overlay im Container: tiefer als ~2000 gibt ENAMETOOLONG
            for name in ("zustand.json", ".weg-alt"):
                with self.subTest(tiefe=tiefe, name=name):
                    baum_weg(o / name)                                 # zustand.json aus dem Unterfall davor
                    tiefer_baum(o / name, tiefe)
                    zahl.clear()
                    vorher = offene_fds()
                    with mock.patch("builtins.print") as ausgabe:
                        self.h.lauf(takt=0, runden=3, sammler=self.s)
                    self.assertEqual(len(zahl), 3)
                    self.assertTrue(json.loads((o / "zustand.json").read_text())["helfer"])
                    self.assertEqual(ausgabe.call_args_list, [])               # keine Runde ist in den Fehlerzweig gelaufen
                    if vorher is not None:
                        self.assertEqual(offene_fds(), vorher)
                    for e in list(os.scandir(o)):                          # für den nächsten Unterfall
                        if e.name.startswith(".weg-"):
                            baum_weg(Path(e.path))

    def test_baum_entfernen_flacher_baum_ganz_weg_tiefer_wirft_nicht(self):
        o = self.h.ordner()
        self._baeume_spaeter_weg(o)
        tiefer_baum(o / ".weg-flach", 50)
        self.h._baum_entfernen(o / ".weg-flach")
        self.assertFalse((o / ".weg-flach").exists())
        tiefer_baum(o / ".weg-tief", 1500)
        vorher = offene_fds()
        self.h._baum_entfernen(o / ".weg-tief")                            # darf liegen bleiben, wirft aber nie
        if vorher is not None:
            self.assertEqual(offene_fds(), vorher)

    def _draussen(self):
        draussen = self.daten / "draussen"
        draussen.mkdir()
        (draussen / "wichtig.txt").write_text("bleibt")
        (draussen / "sub").mkdir()
        (draussen / "sub" / "y").write_text("bleibt auch")
        draussen.chmod(0o555)
        self.addCleanup(draussen.chmod, 0o700)
        return draussen

    def test_symlink_tausch_mitten_im_loeschen_fasst_nichts_draussen_an(self):
        """Der Container tauscht einen Unterordner gegen einen Symlink nach draußen, nachdem der Helfer ihn als Ordner
        erkannt hat (nach stat/lstat) oder kurz bevor er ihn öffnet. Draußen bleibt alles, wie es war."""
        gegriffen = set()
        for haken in ("stat", "lstat", "open", "scandir", "listdir", "chmod"):
            with self.subTest(haken=haken):
                self.tmp2 = tempfile.TemporaryDirectory()
                self.addCleanup(self.tmp2.cleanup)
                self.daten = Path(self.tmp2.name)
                draussen = self._draussen()
                baum = self.daten / "baum"
                (baum / "sub" / "tiefer").mkdir(parents=True)
                (baum / "sub" / "tiefer" / "z").write_text("z")
                (baum / "datei").write_text("d")
                getauscht = []

                def tauschen():
                    if not getauscht:
                        getauscht.append(1)
                        gegriffen.add(haken)
                        os.rename(baum / "sub", baum / "sub-alt")
                        os.symlink(draussen, baum / "sub")

                echt = getattr(os, haken)

                def haken_fn(*args, **kw):
                    trifft = args and str(args[0]).rsplit("/", 1)[-1] == "sub" and "sub-alt" not in str(args[0])
                    if trifft and haken in ("open", "scandir", "listdir", "chmod"):
                        tauschen()                                         # direkt davor
                    ergebnis = echt(*args, **kw)
                    if trifft and haken in ("stat", "lstat"):
                        tauschen()                                         # direkt danach
                    return ergebnis
                with mock.patch.object(os, haken, haken_fn):
                    self.h._baum_entfernen(baum)
                self.assertEqual(oct(draussen.stat().st_mode & 0o777), "0o555")
                self.assertEqual((draussen / "wichtig.txt").read_text(), "bleibt")
                self.assertEqual((draussen / "sub" / "y").read_text(), "bleibt auch")
        self.assertTrue(gegriffen, "kein Haken hat gegriffen")

    def test_aufraeumen_wirft_zustand_wird_trotzdem_geschrieben(self):
        o = self.h.ordner()
        zahl = self._zustand_schreibzaehler()
        self.h._aufraeumen = mock.Mock(side_effect=RuntimeError("boom"))
        (o / "auftrag.json").write_text(json.dumps({"id": "a1", "aktion": "neustart", "name": "jellyfin"}))
        self.h.lauf(takt=0, runden=2, sammler=self.s)
        self.assertEqual(len(zahl), 2)
        self.assertEqual(self._nach_runde_ok(o)["letzte_antwort"]["id"], "a1")

    # ------------------------------------------------ Fix-Welle nach dem Gesamt-Review

    def test_gescheiterter_reboot_nimmt_die_wartung_zurueck(self):
        o = self.h.ordner()
        vorher = self.h.ausfuehren

        def mit_sudo(befehl, zeitlimit=20):
            if befehl == ["sudo", "-n", "-l", "/usr/bin/systemctl", "reboot"]:
                return 0, "", ""
            if befehl == ["sudo", "-n", "/usr/bin/systemctl", "reboot"]:
                return 1, "", "Failed to reboot: Access denied"
            return vorher(befehl, zeitlimit)
        self.h.ausfuehren = mit_sudo
        (o / "auftrag.json").write_text(json.dumps({"id": "r2", "aktion": "host_neustart"}))
        self.h.lauf(takt=0, runden=1, sammler=self.s)
        self.assertFalse(os.path.lexists(o / "wartung.json"))              # die Wache schweigt nicht grundlos
        zeilen = [json.loads(z) for z in (o / "verlauf.jsonl").read_text().splitlines()]
        self.assertEqual((zeilen[-1]["aktion"], zeilen[-1]["ok"]), ("host_neustart", False))
        self.assertIn("Neustart-Befehl scheiterte", zeilen[-1]["text"])
        self.assertIn("Access denied", zeilen[-1]["text"])

    def test_werfender_sammler_laesst_die_antwort_trotzdem_raus(self):
        o = self.h.ordner()
        self.h.ZUSTAND_S = 0                                               # jede Runde neu sammeln
        self.s.zustand = mock.Mock(side_effect=[{"helfer": True, "container": ["gut"]}, OverflowError("inf")])
        auftrag = lambda _s: (o / "auftrag.json").write_text(json.dumps({"id": "w1", "aktion": "gibtsnicht"}))
        with mock.patch("builtins.print"), mock.patch("time.sleep", side_effect=auftrag):   # nach Runde 1
            self.h.lauf(takt=0, runden=2, sammler=self.s)
        z = json.loads((o / "zustand.json").read_text())
        self.assertEqual(z["letzte_antwort"]["id"], "w1")
        self.assertEqual(z["container"], ["gut"])                          # der letzte gute Zustand bleibt
        self.assertAlmostEqual(z["aktualisiert"], time.time(), delta=5)

    def test_werfender_sammler_von_anfang_an(self):
        o = self.h.ordner()
        self.s.zustand = mock.Mock(side_effect=OverflowError("inf"))
        (o / "auftrag.json").write_text(json.dumps({"id": "w2", "aktion": "gibtsnicht"}))
        with mock.patch("builtins.print"):
            self.h.lauf(takt=0, runden=2, sammler=self.s)
        z = json.loads((o / "zustand.json").read_text())
        self.assertEqual(z["letzte_antwort"]["id"], "w2")
        self.assertIn("aktualisiert", z)


class OrdnerGepinnt(SchleifenBasis):
    """`data/hphome` wird einmal geöffnet und über den fd benutzt. Tauscht der Container den Ordner gegen einen
    Symlink (oder verschiebt ihn), schreibt, liest und löscht der Helfer nie im neuen Ziel."""

    def _aussen(self):
        aussen = self.daten / "aussen"
        aussen.mkdir()
        (aussen / "x.tmp").write_text("alt")
        os.utime(aussen / "x.tmp", (time.time() - 3600, time.time() - 3600))
        (aussen / ".weg-1").mkdir()
        (aussen / ".weg-1" / "y").write_text("y")
        (aussen / "auftrag.json").write_text(json.dumps({"id": "fremd", "aktion": "neustart", "name": "jellyfin"}))
        return aussen

    def _inhalt(self, ordner_):
        return sorted((str(p.relative_to(ordner_)), p.read_text() if p.is_file() else None) for p in ordner_.rglob("*"))

    def _nach_runde_tauschen(self, tauschen, runden):
        """Runde 1 läuft normal; in der Pause danach wird getauscht (mitten in einem laufenden `lauf`)."""
        getauscht = []

        def schlafen(_s):
            if not getauscht:
                getauscht.append(1)
                tauschen()
        with mock.patch("time.sleep", side_effect=schlafen), mock.patch("builtins.print") as ausgabe:
            self.h.lauf(takt=0, runden=runden, sammler=self.s)
        self.assertTrue(getauscht)
        return ausgabe

    def test_symlink_statt_ordner_nichts_passiert_draussen(self):
        self.h.ordner()
        aussen = self._aussen()
        vorher = self._inhalt(aussen)

        def tauschen():
            os.rename(self.daten / "hphome", self.daten / "hphome-alt")
            (self.daten / "hphome").symlink_to(aussen, target_is_directory=True)
        ausgabe = self._nach_runde_tauschen(tauschen, 3)
        self.assertEqual(self._inhalt(aussen), vorher)                     # nichts neu, nichts gelöscht, nichts gelesen
        self.assertFalse((aussen / "zustand.json").exists())
        self.assertTrue(ausgabe.call_args_list)                            # eine Zeile ins Log
        self.assertEqual(len(ausgabe.call_args_list), 1)                   # und nicht jede Runde wieder

    def test_echter_neuer_ordner_wird_neu_gepinnt(self):
        self.h.ordner()

        def tauschen():
            os.rename(self.daten / "hphome", self.daten / "hphome-alt")
            (self.daten / "hphome").mkdir()
        self._nach_runde_tauschen(tauschen, 3)
        self.assertTrue(json.loads((self.daten / "hphome" / "zustand.json").read_text())["helfer"])
        self.assertTrue(os.path.samefile(self.h.ordner(), self.daten / "hphome"))

    def test_verschwundener_ordner_wird_neu_angelegt(self):
        self.h.ordner()
        self._nach_runde_tauschen(lambda: os.rename(self.daten / "hphome", self.daten / "hphome-alt"), 3)
        self.assertFalse((self.daten / "hphome").is_symlink())
        self.assertTrue(json.loads((self.daten / "hphome" / "zustand.json").read_text())["helfer"])

    def test_ordner_ist_der_gepinnte_fd_ein_fd_je_datenordner(self):
        vorher = offene_fds()
        a, b = self.h.ordner(), self.h.ordner()
        self.assertEqual(a, b)
        self.assertTrue(str(a).startswith("/proc/self/fd/"))
        if vorher is not None:
            self.assertEqual(offene_fds(), vorher + 1)
        self.h._ordner_schliessen()
        if vorher is not None:
            self.assertEqual(offene_fds(), vorher)

    def test_ohne_proc_scheitert_es_mit_klarer_meldung(self):
        self.h.PROC_FD = str(self.daten / "gibt-es-nicht")
        vorher = offene_fds()
        with self.assertRaises(OSError) as f:
            self.h.ordner()
        self.assertIn("/proc", str(f.exception))
        if vorher is not None:
            self.assertEqual(offene_fds(), vorher)                         # kein fd bleibt hängen


if __name__ == "__main__":
    unittest.main()
