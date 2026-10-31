"""Erzeugt web/mc/bloecke.json: für jedes Item, das im Spiel als voller Würfel gezeichnet wird, die Texturen von
oben, links (Süd) und rechts (Ost) — gelesen aus den Modellen im Client-Jar von Minecraft.

    python3 -I scripts/mc_bloecke.py <minecraft-client.jar>

Blöcke ohne vollen Würfel (Treppen, Zäune …) bekommen nur `flach` (ihre Haupttextur): die Karte zeigt sie flach.
"""
import json
import sys
import zipfile

jar = zipfile.ZipFile(sys.argv[1])
A = "assets/minecraft/"
NAMEN = set(jar.namelist())


def lesen(pfad):
    return json.loads(jar.read(pfad)) if pfad in NAMEN else None


def modellname(wurzel):
    """Das erste einfache Modell eines Item-Eintrags (items/<id>.json)."""
    if isinstance(wurzel, dict):
        if wurzel.get("type") == "minecraft:model" and isinstance(wurzel.get("model"), str):
            return wurzel["model"], wurzel.get("tints")
        for v in wurzel.values():
            r = modellname(v)
            if r:
                return r
    elif isinstance(wurzel, list):
        for v in wurzel:
            r = modellname(v)
            if r:
                return r
    return None


def auflösen(name):
    """Kette der Modelle von unten nach oben: (Texturen, erste Elementliste)."""
    texturen, elemente = {}, None
    while name:
        m = lesen(f"{A}models/{name.replace('minecraft:', '')}.json")
        if not m:
            return None
        for k, v in m.get("textures", {}).items():
            texturen.setdefault(k, v)
        if elemente is None and m.get("elements"):
            elemente = m["elements"]
        name = m.get("parent")
    return texturen, elemente


def textur(t, texturen):
    for _ in range(6):
        if not isinstance(t, str) or not t.startswith("#"):
            break
        t = texturen.get(t[1:])
    if not isinstance(t, str):
        return None
    t = t.replace("minecraft:", "")
    return t[len("block/"):] if t.startswith("block/") and f"{A}textures/{t}.png" in NAMEN else None


def tint(tints):
    if not tints:
        return None
    t = tints[0]
    if t.get("type") == "minecraft:grass":
        return "#7cbd6b", True                         # Gras: nur die Oberseite, Standardfarbe der Ebene
    if t.get("type") == "minecraft:foliage":
        return "#59ae30", False
    if t.get("type") == "minecraft:constant" and isinstance(t.get("value"), int):
        return "#%06x" % (t["value"] & 0xFFFFFF), False
    return None


aus = {}
for n in sorted(NAMEN):
    if not (n.startswith(A + "items/") and n.endswith(".json")):
        continue
    kennung = n[len(A + "items/"):-5]
    erg = modellname(lesen(n).get("model"))
    if not erg or not erg[0].startswith("minecraft:block/"):
        continue
    kette = auflösen(erg[0])
    if not kette or not kette[1]:
        continue
    texturen, elemente = kette
    e = elemente[0]
    if len(elemente) > 2 or e.get("from") != [0, 0, 0] or e.get("to") != [16, 16, 16]:
        flach = textur("#particle", texturen)                  # Treppen, Zäune, Platten …: flach mit ihrer Haupttextur
        if flach:
            aus[kennung] = {"flach": flach}
        continue
    f = e.get("faces", {})
    t = [textur(f.get(s, {}).get("texture"), texturen) for s in ("up", "south", "east")]
    if not all(t):
        continue
    aus[kennung] = {"o": t[0], "l": t[1], "r": t[2]}
    ti = tint(erg[1])
    if ti:
        aus[kennung]["tint"] = ti[0]
        if ti[1]:
            aus[kennung]["nur_oben"] = 1
json.dump(aus, open("web/mc/bloecke.json", "w"), separators=(",", ":"), sort_keys=True)
print(len(aus), "Einträge,", sum(1 for v in aus.values() if "o" in v), "Würfel")
