"""Misst einen OpenAI-kompatiblen Embedding-Dienst: Tempo und deutsche Trefferquote (erfundene Sätze).

Aufruf: python3 scripts/embedder_messen.py [URL] [MODELL]
Zwei Sätze: sauberes Deutsch und Chat-Slang mit Tippfehlern (wie echte Nachrichten). Gemessen wird, ob zur Frage der
passende Fakt unter ALLEN Fakten (beider Sätze) auf Platz 1 liegt.
"""
import json
import statistics
import sys
import time
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/v1/embeddings"
MODELL = sys.argv[2] if len(sys.argv) > 2 else "x"

# (Frage, passender Fakt) — erfunden, alle Namen frei.
SAUBER = [
    ("Welches Instrument spielt Jule?", "Jule spielt seit drei Jahren Cello im Jugendorchester Nordlicht."),
    ("Wo wohnt Omar?", "Omar wohnt in Wiesenau und fährt mit dem Bus 42 zur Schule."),
    ("Was unterrichtet Frau Brandt?", "Frau Brandt unterrichtet Chemie und Sport an der Realschule Birkenhain."),
    ("Wie heißt Tobis Hund?", "Tobi hat einen Beagle namens Keks."),
    ("Wo macht Clara ein Praktikum?", "Clara macht im Frühjahr ein Praktikum bei der Bäckerei Sonnenkorn."),
    ("Wogegen ist Emil allergisch?", "Emil verträgt keine Haselnüsse."),
    ("Wohin fährt Pia im Urlaub?", "Pia reist im August mit ihrer Familie nach Slowenien."),
    ("Bei welchem Verein spielt Finn?", "Finn ist Torwart beim FC Eintracht Moorbach."),
    ("Worüber schreibt Nele eine Klausur?", "Nele schreibt am Donnerstag eine Geschichtsklausur zur Weimarer Republik."),
    ("Wer hilft Jonas beim Baumhaus?", "Jonas baut mit seinem Onkel Rolf ein Baumhaus."),
    ("Wann hat Sofie Geburtstag?", "Sofie wird am 3. November 15 Jahre alt."),
    ("Was programmiert Mats?", "Mats entwickelt in der Informatik-AG ein Spiel in Python."),
]
SLANG = [
    ("was zockt Kian so", "Kian zockt voll viel Fortnite mit seinen Jungs, meistens abends."),
    ("wo is Lara eig am wochenende", "Lara is am WE bei ihrer Oma in Hildesheim."),
    ("wer is der neue in der Klasse", "Der neue heißt Dennis und kommt aus Köln, sitzt neben Kontakt Fine."),
    ("hat Ali nen Führerschein", "Ali hat letzte Woche endlich seinen Führerschein bestanden lol."),
    ("was is mit Mias Handy", "Mias Handy is kaputt gegangen, Display komplett hinüber."),
    ("wann is die Mathearbeit", "Mathearbeit is nächsten Mittwoch, Thema Integralrechnung."),
    ("wer holt Lukas ab", "Lukas wird heute von seinem Papa abgeholt, weil Training ausfällt."),
    ("was mag Zoe zu essen", "Zoe liebt Döner und hasst Pilze."),
    ("wo arbeitet Leons Mutter", "Leons Kontakt P schafft im Krankenhaus als Pflegerin."),
    ("welche Band hört Hanna", "Hanna hört nur noch Provinz und Wincent Weiss, nervt voll."),
    ("wann fährt Kontakt A zum Ski", "Kontakt A fährt in den Winterferien mit der Schule zum Skifahren nach Österreich."),
    ("wem gehört der blaue Hoodie", "Der blaue Hoodie gehört Nico, hat ihn bei Eike vergessen."),
]


def einbetten(texte):
    anfrage = urllib.request.Request(URL, json.dumps({"model": MODELL, "input": texte}).encode(),
                                     {"Content-Type": "application/json"})
    daten = json.loads(urllib.request.urlopen(anfrage, timeout=300).read())["data"]
    return [d["embedding"] for d in sorted(daten, key=lambda d: d["index"])]


def kosinus(a, b):
    z = sum(x * y for x, y in zip(a, b))
    return z / ((sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5))


alle = SAUBER + SLANG
fragen = einbetten([f for f, _ in alle])
fakten = einbetten([s for _, s in alle])


def platz1(von, bis):
    return sum(1 for i in range(von, bis)
               if max(range(len(fakten)), key=lambda j: kosinus(fragen[i], fakten[j])) == i)


sauber, slang = platz1(0, len(SAUBER)), platz1(len(SAUBER), len(alle))
zeiten = []
for _ in range(20):
    t = time.time()
    einbetten(["Jule spielt seit drei Jahren Cello im Jugendorchester Nordlicht."])
    zeiten.append(time.time() - t)
print(f"Dimension {len(fragen[0])}; Platz 1 sauber {sauber}/{len(SAUBER)}, Slang {slang}/{len(SLANG)} "
      f"(unter allen {len(alle)} Fakten); Einzelaufruf Median {statistics.median(zeiten) * 1000:.0f} ms")
