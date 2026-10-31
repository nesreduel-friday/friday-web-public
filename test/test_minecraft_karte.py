"""Karte „Minecraft“: Meldung der Mod annehmen, zurückschneiden, veralten lassen, Schlüssel prüfen."""

import asyncio
import os
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from kern.dashboard import minecraft


def _welt(**extra):
    d = {"zustand": "welt", "version": "26.3", "loader": "Fabric", "mods": 31, "name": "Neue Welt", "dimension": "overworld",
         "x": 12, "y": 64, "z": -3, "tageszeit": 6000, "leben": 17.5, "leben_max": 20, "hunger": 18,
         "ruestung": {"kopf": "diamond_helmet"}, "hotbar": [{"id": "diamond_pickaxe", "anzahl": 1}], "auswahl": 0,
         "hand": {"id": "diamond_pickaxe", "anzahl": 1}}
    d.update(extra)
    return d


class MinecraftKarte(unittest.TestCase):
    def setUp(self):
        minecraft._reset()

    def test_ohne_meldung_keine_karte(self):
        self.assertEqual(asyncio.run(minecraft.karten()), [])

    def test_welt_wird_karte(self):
        minecraft.annehmen(_welt())
        k = asyncio.run(minecraft.karten())
        self.assertEqual(len(k), 1)
        self.assertEqual((k[0]["art"], k[0]["name"], k[0]["x"], len(k[0]["hotbar"])), ("minecraft", "Neue Welt", 12, 9))
        self.assertEqual(k[0]["ruestung"]["kopf"], "diamond_helmet")
        self.assertEqual(k[0]["ruestung"]["brust"], "")

    def test_menu_hat_nur_die_kopfdaten(self):
        minecraft.annehmen({"zustand": "menu", "version": "26.3", "loader": "Fabric", "mods": 31, "x": 99})
        k = asyncio.run(minecraft.karten())[0]
        self.assertEqual(k["zustand"], "menu")
        self.assertNotIn("x", k)

    def test_aus_nimmt_die_karte_weg(self):
        minecraft.annehmen(_welt())
        minecraft.annehmen({"zustand": "aus"})
        self.assertEqual(asyncio.run(minecraft.karten()), [])

    def test_veraltet_nach_der_frist(self):
        minecraft.annehmen(_welt())
        with mock.patch("kern.dashboard.minecraft.time.time", return_value=time.time() + minecraft.VERALTET_S + 1):
            self.assertEqual(asyncio.run(minecraft.karten()), [])

    def test_fremde_felder_und_boese_kennungen_fliegen_raus(self):
        minecraft.annehmen(_welt(hand={"id": "../../etc", "anzahl": 1}, skin_url="https://boese.example/x.png", extra="x"))
        k = asyncio.run(minecraft.karten())[0]
        self.assertEqual(k["hand"]["id"], "")
        self.assertEqual(k["skin_url"], "")
        self.assertNotIn("extra", k)

    def test_nur_die_mojang_adresse_gilt_als_skin(self):
        url = "https://textures.minecraft.net/texture/" + "ab" * 32
        minecraft.annehmen(_welt(skin_url=url))
        self.assertEqual(asyncio.run(minecraft.karten())[0]["skin_url"], url)

    def test_eigener_skin_als_base64_wird_gemerkt_und_ausgeliefert(self):
        import base64
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
        minecraft.annehmen(_welt(skin_pfad="skins/abc123", skin_png=base64.b64encode(png).decode()))
        self.assertTrue(asyncio.run(minecraft.karten())[0]["skin_da"])
        self.assertEqual(asyncio.run(minecraft.skin_holen()), png)
        minecraft.annehmen(_welt(skin_pfad="skins/abc123"))            # nächste Meldung ohne Bild: der gemerkte bleibt
        self.assertEqual(asyncio.run(minecraft.skin_holen()), png)

    def test_skin_der_kein_png_ist_wird_verworfen(self):
        import base64
        minecraft.annehmen(_welt(skin_pfad="skins/abc123", skin_png=base64.b64encode(b"<html>boese</html>").decode()))
        self.assertFalse(asyncio.run(minecraft.karten())[0]["skin_da"])
        self.assertIsNone(asyncio.run(minecraft.skin_holen()))

    def test_inventar_hat_immer_27_felder(self):
        minecraft.annehmen(_welt(inventar=[{"id": "stone", "anzahl": 64}]))
        inv = asyncio.run(minecraft.karten())[0]["inventar"]
        self.assertEqual((len(inv), inv[0]["id"], inv[26]["id"]), (27, "stone", ""))

    def test_unbekannter_zustand_wird_abgelehnt(self):
        with self.assertRaises(ValueError):
            minecraft.annehmen({"zustand": "wasanderes"})


class MinecraftRoute(unittest.TestCase):
    def setUp(self):
        minecraft._reset()
        from kern.main import app
        self.c = TestClient(app)

    def test_schluessel_wird_geprueft(self):
        with mock.patch.dict(os.environ, {"MINECRAFT_SCHLUESSEL": "geheim"}):
            self.assertEqual(self.c.post("/api/minecraft", json=_welt()).status_code, 401)
            self.assertEqual(self.c.post("/api/minecraft", json=_welt(), headers={"Authorization": "Bearer falsch"}).status_code, 401)
            self.assertIsNone(minecraft.stand())
            self.assertEqual(self.c.post("/api/minecraft", json=_welt(), headers={"Authorization": "Bearer geheim"}).status_code, 200)
            self.assertEqual(minecraft.stand()["name"], "Neue Welt")

    def test_ohne_schluessel_in_env_geht_nichts(self):
        with mock.patch.dict(os.environ, {"MINECRAFT_SCHLUESSEL": ""}):
            self.assertEqual(self.c.post("/api/minecraft", json=_welt(), headers={"Authorization": "Bearer "}).status_code, 503)


if __name__ == "__main__":
    unittest.main()
