import asyncio
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WURZEL = Path(__file__).resolve().parent.parent
SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
AUF_DEM_HOST = (WURZEL / "docker-compose.yml").exists()


def _runner():
    pfad = WURZEL / "werkbank-sandbox" / "runner.py"
    spec = importlib.util.spec_from_file_location("werkbank_runner", pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _dienstblock(text: str, name: str) -> str:
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|^\S|\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"FRIDAY_DATEN": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)
        from kern.werkbank import opencode
        self.oc = opencode
        t = mock.patch.object(opencode, "TAKT_S", 0.02)
        t.start()
        self.addCleanup(t.stop)

    def _attrappe(self, inhalt: dict | str):
        """Ersetzt den Läufer: wartet auf in.json und schreibt out.json."""
        async def lauf():
            wurzel = Path(self.tmp.name) / "werkbank" / "sandbox"
            for _ in range(200):
                for o in wurzel.glob("*/in.json") if wurzel.exists() else []:
                    if (o.parent / "out.json").exists() or (o.parent / "out.json").is_symlink():
                        continue
                    eingabe = json.loads(o.read_text())
                    self.eingabe = eingabe
                    (o.parent / "out.json").write_text(inhalt if isinstance(inhalt, str) else json.dumps(inhalt))
                    return
                await asyncio.sleep(0.01)
        return lauf()

    def _anfrage(self, inhalt, **kw):
        async def ganz():
            t = asyncio.create_task(self._attrappe(inhalt))
            try:
                return await self.oc.json_anfrage("Frage", SCHEMA, **kw)
            finally:
                await t
        return asyncio.run(ganz())

    def test_json_anfrage_liest_ergebnis(self):
        d = self._anfrage({"ok": True, "text": "x", "json": {"a": "b"}, "fehler": ""}, modell="m")
        self.assertEqual(d, {"a": "b"})
        self.assertEqual(self.eingabe, {"prompt": "Frage", "schema": SCHEMA, "modell": "m"})
        ordner = next((Path(self.tmp.name) / "werkbank" / "sandbox").iterdir())
        self.assertRegex(ordner.name, r"^[0-9a-f]{16}$")
        self.assertEqual(oct((ordner / "in.json").stat().st_mode & 0o777), "0o644")

    def test_ablegen_folgt_keinem_symlink(self):
        """Ein untergeschobener Symlink statt des Auftragsordners: kein chmod am Ziel, keine in.json dort."""
        ziel = Path(self.tmp.name) / "fremd"
        ziel.mkdir()
        os.chmod(ziel, 0o700)
        ordner = Path(self.tmp.name) / "werkbank" / "sandbox" / "0123456789abcdef"
        ordner.parent.mkdir(parents=True)
        ordner.symlink_to(ziel)
        with self.assertRaises(OSError):
            self.oc._ablegen(ordner, "Frage", SCHEMA, "")
        self.assertEqual(oct(ziel.stat().st_mode & 0o777), "0o700")
        self.assertEqual(list(ziel.iterdir()), [])
        # Gegenprobe: ein echter Ordner wird 0777, in.json 0644
        echt = ordner.parent / "fedcba9876543210"
        self.oc._ablegen(echt, "Frage", SCHEMA, "")
        self.assertEqual(oct(echt.stat().st_mode & 0o777), "0o777")
        self.assertEqual(oct((echt / "in.json").stat().st_mode & 0o777), "0o644")
        self.assertFalse((echt / "in.tmp").exists())

    def test_timeout_wirft_klartext(self):
        with self.assertRaises(self.oc.OpenCodeFehler) as e:
            asyncio.run(self.oc.json_anfrage("Frage", SCHEMA, frist_s=0))
        self.assertIn("nicht innerhalb", str(e.exception))

    def test_kaputtes_json_wirft(self):
        with self.assertRaises(self.oc.OpenCodeFehler):
            self._anfrage("{kaputt")
        with self.assertRaises(self.oc.OpenCodeFehler):
            self._anfrage("[1, 2]")

    def test_fehler_der_sandbox_wird_klartext(self):
        with self.assertRaises(self.oc.OpenCodeFehler) as e:
            self._anfrage({"ok": False, "text": "", "json": None, "fehler": "leere Antwort"})
        self.assertIn("leere Antwort", str(e.exception))

    def test_symlink_als_antwort_wird_abgelehnt(self):
        geheim = Path(self.tmp.name) / "geheim.json"
        geheim.write_text('{"ok": true, "json": {"a": "x"}}')
        async def lauf():
            wurzel = Path(self.tmp.name) / "werkbank" / "sandbox"
            for _ in range(200):
                for o in wurzel.glob("*/in.json") if wurzel.exists() else []:
                    (o.parent / "out.json").symlink_to(geheim)
                    return
                await asyncio.sleep(0.01)
        async def ganz():
            t = asyncio.create_task(lauf())
            try:
                return await self.oc.json_anfrage("F", SCHEMA)
            finally:
                await t
        with self.assertRaises(self.oc.OpenCodeFehler):
            asyncio.run(ganz())

    def test_zu_grosse_antwort_wird_abgelehnt(self):
        with mock.patch.object(self.oc, "MAX_ANTWORT", 50):
            with self.assertRaises(self.oc.OpenCodeFehler) as e:
                self._anfrage({"ok": True, "text": "x" * 200, "json": {"a": "b"}, "fehler": ""})
        self.assertIn("zu groß", str(e.exception))


class RunnerTest(unittest.TestCase):
    def test_runner_wiederholt_bei_kaputtem_json_zweimal(self):
        r = _runner()
        antworten = [subprocess.CompletedProcess([], 0, json.dumps({"type": "text", "part": {"text": t}}), "")
                     for t in ("kein json", '{"b": 1}', '{"a": "gut"}')]
        with mock.patch.object(r.subprocess, "run", side_effect=antworten) as run, mock.patch.object(r.time, "sleep"):
            e = r.beantworten("Frage", SCHEMA, "m")
        self.assertTrue(e["ok"])
        self.assertEqual(e["json"], {"a": "gut"})
        self.assertEqual(run.call_count, 3)
        args = run.call_args.args[0]
        self.assertEqual(args[:4], ["opencode", "run", "-m", "opencode/m"])
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_runner_gibt_nach_drei_versuchen_auf(self):
        r = _runner()
        kaputt = subprocess.CompletedProcess([], 0, json.dumps({"type": "text", "part": {"text": "nix"}}), "")
        with mock.patch.object(r.subprocess, "run", return_value=kaputt) as run, mock.patch.object(r.time, "sleep"):
            e = r.beantworten("Frage", SCHEMA, "")
        self.assertFalse(e["ok"])
        self.assertEqual(run.call_count, 3)
        self.assertIn("3 Versuchen", e["fehler"])

    def test_runner_lehnt_symlink_auftrag_ab(self):
        r = _runner()
        with tempfile.TemporaryDirectory() as t:
            ziel = Path(t) / "ziel"
            ziel.mkdir()
            (ziel / "in.json").write_text('{"prompt": "x", "schema": null, "modell": ""}')
            (Path(t) / "0123456789abcdef").symlink_to(ziel)
            with mock.patch.object(r.subprocess, "run") as run:
                self.assertFalse(r.auftrag_bearbeiten(t, "0123456789abcdef"))
            run.assert_not_called()
            self.assertFalse((ziel / "out.json").exists())

    def test_runner_schreibt_out_json(self):
        r = _runner()
        with tempfile.TemporaryDirectory() as t:
            o = Path(t) / "0123456789abcdef"
            o.mkdir()
            (o / "in.json").write_text(json.dumps({"prompt": "x", "schema": SCHEMA, "modell": ""}))
            ok = subprocess.CompletedProcess([], 0, json.dumps({"type": "text", "part": {"text": '{"a": "z"}'}}), "")
            with mock.patch.object(r.subprocess, "run", return_value=ok):
                self.assertTrue(r.auftrag_bearbeiten(t, o.name))
            self.assertEqual(json.loads((o / "out.json").read_text())["json"], {"a": "z"})
            with mock.patch.object(r.subprocess, "run") as run:       # zweiter Durchgang: schon erledigt
                self.assertFalse(r.auftrag_bearbeiten(t, o.name))
            run.assert_not_called()


def _ohne_kommentare(block: str) -> str:
    return "\n".join(z for z in block.splitlines() if not z.strip().startswith("#"))


def _umgebung(code: str) -> dict:
    m = re.search(r"^    environment:\n((?:      .*\n?)+)", code, re.M)
    paare = {}
    for z in (m.group(1).splitlines() if m else []):
        k, _, v = z.strip().partition(":")
        paare[k.strip()] = v.strip().strip('"')
    return paare


def _netze(code: str) -> list[str]:
    m = re.search(r"^    networks:\n((?:      - .*\n?)+)", code, re.M)
    return [z.strip()[2:] for z in (m.group(1).splitlines() if m else [])]


PROXY_VARIABLEN = {"HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "NO_PROXY", "no_proxy"}


@unittest.skipUnless(AUF_DEM_HOST, "docker-compose.yml nicht eingebunden")
class ComposeTest(unittest.TestCase):
    def setUp(self):
        self.text = (WURZEL / "docker-compose.yml").read_text(encoding="utf-8")

    def _pruefe(self, text: str):
        block = _dienstblock(text, "werkbank-sandbox")
        self.assertTrue(block, "Dienst werkbank-sandbox fehlt")
        code = _ohne_kommentare(block)
        self.assertNotIn("env_file", code)
        self.assertNotIn("FRIDAY_", code)
        self.assertNotIn("docker.sock", code)
        self.assertEqual({k for k in _umgebung(code) if k not in PROXY_VARIABLEN}, set(), "fremde Umgebungsvariable")
        for k in _umgebung(code):
            self.assertNotRegex(k, r"(?i)KEY|TOKEN|SECRET|PASS|FRIDAY_")
        self.assertEqual(re.findall(r"^\s+- (\./\S+|/\S+):/\S+", code, re.M), [])      # keine Kurzform
        self.assertEqual(re.findall(r"^\s+source: (\S+)", code, re.M), ["./data/werkbank/sandbox", "./docs", "./AGENTS.md", "./CLAUDE.md"])
        self.assertEqual(re.findall(r"^\s+- type: (\S+)", code, re.M), ["bind"] * 4)
        self.assertEqual(code.count("read_only: true"), 4)                             # Container + 3 Doku-Mounts, nichts anderes lesbar
        self.assertIn("create_host_path: false", code)                                # Docker legt nichts als root an
        self.assertIn('profiles: ["werkbank"]', code)
        self.assertIn("cap_drop: [ALL]", code)
        self.assertIn("no-new-privileges:true", code)
        self.assertIn("read_only: true", code)
        self.assertNotIn("network_mode", code)
        self.assertEqual(_netze(code), ["werkbank-sandbox"])
        return code

    def test_compose_dienst_hat_keinen_data_mount_und_keine_env_datei(self):
        code = self._pruefe(self.text)
        self.assertEqual(_umgebung(code)["HTTPS_PROXY"], "http://werkbank-proxy:3128")
        self.assertEqual(_umgebung(code)["NO_PROXY"], "")

    def test_gegenprobe_schluessel_in_umgebung_wird_erkannt(self):
        boese = self.text.replace("      NO_PROXY: \"\"\n", "      NO_PROXY: \"\"\n      GEMINI_API_KEY: abc\n", 1)
        self.assertNotEqual(boese, self.text)
        with self.assertRaises(AssertionError):
            self._pruefe(boese)

    def test_sandbox_netz_ist_intern_und_nur_der_proxy_hat_zwei_netze(self):
        m = re.search(r"^networks:\n((?:  .*\n?|\n)+)", self.text, re.M)
        netze = m.group(1)
        self.assertRegex(netze, r"(?m)^  werkbank-sandbox:\n    internal: true$")
        proxy = _ohne_kommentare(_dienstblock(self.text, "werkbank-proxy"))
        self.assertEqual(sorted(_netze(proxy)), ["werkbank-aussen", "werkbank-sandbox"])
        self.assertNotRegex(netze, r"(?m)^  werkbank-aussen:\n    internal")
        for name in ("werkbank-proxy",):
            code = _ohne_kommentare(_dienstblock(self.text, name))
            self.assertIn('profiles: ["werkbank"]', code)
            self.assertIn("pull_policy: never", code)                 # lokales Image, nie aus einer Registry
            self.assertIn("read_only: true", code)
            self.assertIn("cap_drop: [ALL]", code)
            self.assertIn("no-new-privileges:true", code)
            self.assertNotIn("env_file", code)
            self.assertNotIn("volumes:", code)
            self.assertNotIn("environment:", code)
        # Kein anderer Dienst hängt am Sandbox-Netz
        for dienst in re.findall(r"^  ([A-Za-z0-9_-]+):\n", self.text, re.M):
            if dienst not in ("werkbank-sandbox", "werkbank-proxy"):
                self.assertNotIn("werkbank-sandbox", _ohne_kommentare(_dienstblock(self.text, dienst)).split("networks:")[-1]
                                 if "networks:" in _dienstblock(self.text, dienst) else "")

    def test_sandbox_dockerfile_pinnt_opencode(self):
        d = (WURZEL / "werkbank-sandbox" / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(d, r"OPENCODE_VERSION=\d+\.\d+\.\d+")
        self.assertIn("vor dem ersten Bauen", d)
        self.assertNotIn("COPY . ", d)
        self.assertIn("proxy.py", d)


def _proxy():
    pfad = WURZEL / "werkbank-sandbox" / "proxy.py"
    spec = importlib.util.spec_from_file_location("werkbank_proxy", pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _aufloeser(*adressen):
    def f(host, port, type=0):
        return [(2, 1, 6, "", (a, port)) for a in adressen]
    return f


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self.p = _proxy()

    def _abgelehnt(self, host, port=443, aufloeser=None):
        with self.assertRaises(self.p.Abgelehnt):
            self.p.ziel_pruefen(host, port, aufloeser or _aufloeser("93.184.216.34"))

    def test_erlaubte_hosts(self):
        for h in ("opencode.ai", "api.opencode.ai", "models.dev", "OpenCode.ai."):
            self.assertEqual(self.p.ziel_pruefen(h, 443, _aufloeser("93.184.216.34")), ["93.184.216.34"], h)

    def test_fremde_hosts_und_ip_literale_abgelehnt(self):
        for h in ("evil.example", "localhost", "192.168.178.1", "192.0.2.10", "10.0.0.5", "169.254.169.254",
                  "::1", "[::1]", "evilopencode.ai", "opencode.ai.evil.example", "", "127.0.0.1"):
            self._abgelehnt(h)

    def test_name_der_auf_private_adresse_zeigt_wird_abgelehnt(self):
        for a in ("192.168.1.5", "10.1.2.3", "127.0.0.1", "169.254.169.254", "100.100.1.1", "::1", "fe80::1",
                  "fd00::1", "0.0.0.0", "224.0.0.1", "::ffff:10.0.0.1"):
            self._abgelehnt("opencode.ai", aufloeser=_aufloeser(a))
        self._abgelehnt("opencode.ai", aufloeser=_aufloeser("93.184.216.34", "192.168.1.5"))   # jede Adresse zählt

    def test_port_nicht_443_abgelehnt(self):
        for port in (80, 22, 8123, 8443):
            self._abgelehnt("opencode.ai", port)

    def test_nicht_aufloesbar_abgelehnt(self):
        def kaputt(*a, **k):
            raise OSError("nix")
        self._abgelehnt("opencode.ai", aufloeser=kaputt)

    def test_umgehungsversuche_werden_nie_aufgeloest(self):
        gefragt = []

        def protokoll(host, port, type=0):
            gefragt.append(host)
            return [(2, 1, 6, "", ("93.184.216.34", port))]
        for h in ("evil.example\x00.opencode.ai", "localhost\x00.opencode.ai", "evil.com#.opencode.ai",
                  "*.opencode.ai", "a\u00e4.opencode.ai", "opencode.ai..", ".opencode.ai", "-a.opencode.ai",
                  "a-.opencode.ai", "a b.opencode.ai", "x" * 64 + ".opencode.ai", "a/b.opencode.ai", "a@opencode.ai"):
            with self.assertRaises(self.p.Abgelehnt, msg=repr(h)):
                self.p.ziel_pruefen(h, 443, protokoll)
        self.assertEqual(gefragt, [])

    def test_resolver_bekommt_den_geprueften_namen(self):
        gefragt = []

        def protokoll(host, port, type=0):
            gefragt.append(host)
            return [(2, 1, 6, "", ("93.184.216.34", port))]
        self.p.ziel_pruefen("OPENCODE.AI.", 443, protokoll)
        self.p.ziel_pruefen("api.opencode.ai", 443, protokoll)
        self.assertEqual(gefragt, ["opencode.ai", "api.opencode.ai"])

    def test_connect_zeile_streng(self):
        self.assertEqual(self.p.connect_ziel("CONNECT OPENCODE.AI.:443 HTTP/1.1"), ("OPENCODE.AI.", 443))
        self.assertEqual(self.p.connect_ziel("CONNECT api.opencode.ai:443 HTTP/1.1"), ("api.opencode.ai", 443))
        for z in ("CONNECT opencode.ai:\u00b2 HTTP/1.1", "CONNECT opencode.ai:+443 HTTP/1.1",
                  "CONNECT opencode.ai:0443 HTTP/1.1", "CONNECT opencode.ai:443443 HTTP/1.1",
                  "CONNECT evil.com\x00.opencode.ai:443 HTTP/1.1", "CONNECT opencode.ai:443 HTTP/1.1\x7f",
                  "CONNECT opencode.ai:443\x0b HTTP/1.1"):
            with self.assertRaises(self.p.Abgelehnt, msg=repr(z)):
                self.p.connect_ziel(z)

    def test_connect_zeile(self):
        self.assertEqual(self.p.connect_ziel("CONNECT opencode.ai:443 HTTP/1.1"), ("opencode.ai", 443))
        for z in ("GET http://opencode.ai/ HTTP/1.1", "CONNECT opencode.ai HTTP/1.1", "CONNECT [::1]:443 HTTP/1.1",
                  "CONNECT a:b HTTP/1.1", "CONNECT"):
            with self.assertRaises(self.p.Abgelehnt):
                self.p.connect_ziel(z)


class GatewayTest(unittest.TestCase):
    def test_gateway_ist_entfernt(self):
        self.assertFalse((WURZEL / "host-helfer" / "opencode_gateway.py").exists())


if __name__ == "__main__":
    unittest.main()
