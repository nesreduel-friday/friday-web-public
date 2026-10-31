"""Prüft die Suchqualität der Vektoren eines Graphiti-Graphen (nur lesen, ohne Netz und ohne Embedding-Dienst).

Aufruf (im Graphiti-Image):
  docker run --rm --network friday-web_default -v $PWD/scripts:/s:ro \\
    zepai/knowledge-graph-mcp:1.1.0-graphiti-0.30.1-standalone \\
    /app/mcp/.venv/bin/python /s/graph_suche_pruefen.py eike friday-test-kopie-neu
Je Graph zwei Zahlen:
  Selbsttreffer: Wie oft liegt ein Fakt, als Suchanfrage benutzt, unter den Top-3 aller Fakten?
  Nachbarn P@3: Sucht man mit dem Namen einer Person/Sache (ihr eigener gespeicherter Namensvektor), wie viele der
    Top-3-Fakten hängen wirklich an ihr? (Die Wahrheit steht in der Graphstruktur, nicht im Vektorraum.)
Bei zwei Graphen (alt, neu) gilt „gut genug“: Selbsttreffer neu ≥ 95 % UND Nachbarn P@3 neu ≥ 90 % des alten Werts.
"""
import sys


def kosinus(a, b):
    z = sum(x * y for x, y in zip(a, b))
    return z / ((sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5) or 1.0)


def messen(g):
    kanten = g.query("MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) RETURN r.uuid, a.uuid, b.uuid, r.fact_embedding").result_set
    knoten = g.query("MATCH (n:Entity) RETURN n.uuid, n.name_embedding").result_set
    kanten = [k for k in kanten if k[3]]
    selbst = sum(any(x[0] == k[0] for x in sorted(kanten, key=lambda x: -kosinus(k[3], x[3]))[:3]) for k in kanten)
    summe, anzahl = 0.0, 0
    for uuid, vek in knoten:
        eigene = {k[0] for k in kanten if uuid in (k[1], k[2])}
        if not eigene or not vek:
            continue
        top = sorted(kanten, key=lambda x: -kosinus(vek, x[3]))[:3]
        summe += sum(k[0] in eigene for k in top) / 3
        anzahl += 1
    return selbst / len(kanten), summe / max(anzahl, 1), len(kanten), anzahl


def main():
    from falkordb import FalkorDB
    db = FalkorDB(host="falkordb", port=6379)
    ergebnisse = []
    for name in sys.argv[1:]:
        s, p, nk, nn = messen(db.select_graph(name))
        print(f"{name}: Selbsttreffer {s:.0%} ({nk} Fakten), Nachbarn P@3 {p:.0%} ({nn} Knoten)")
        ergebnisse.append((s, p))
    if len(ergebnisse) == 2:
        (_, p_alt), (s_neu, p_neu) = ergebnisse
        gut = s_neu >= 0.95 and p_neu >= 0.9 * p_alt
        print("GUT GENUG" if gut else "NICHT GUT GENUG")
        sys.exit(0 if gut else 1)


if __name__ == "__main__":
    main()
