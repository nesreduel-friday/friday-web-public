"""Rechnet die Vektoren eines Graphiti-Graphen mit dem lokalen Embedder neu — auf einer KOPIE.

Aufruf (im Graphiti-Image, weil dort FalkorDB-Client und Netz vorhanden sind):
  docker run --rm --network friday-web_default -v $PWD/scripts:/s:ro \\
    zepai/knowledge-graph-mcp:1.1.0-graphiti-0.30.1-standalone \\
    /app/mcp/.venv/bin/python /s/graph_neu_einbetten.py --graph eike --ziel eike_neu --dim 384
Der Quellgraph wird nie verändert oder gelöscht; das Umschalten (Tausch der Namen) macht Eike von Hand
(docs/superpowers/plans/2026-10-04-lokaler-embedder.md, Task 5). Wiederholbar: Existiert das Ziel schon, werden
alle Vektoren dort einfach noch einmal gerechnet.
"""
import argparse
import json
import sys
import urllib.request

# Graphiti bildet die Vektoren so (graphiti_core nodes.py/edges.py): Entity.name_embedding aus `name`,
# RELATES_TO.fact_embedding aus `fact`, jeweils mit Leerzeichen statt Zeilenumbruch; gespeichert als vecf32.
ZIELE = (
    ("Entity", "(n:Entity)", "name_embedding", "name"),
    ("RELATES_TO", "()-[n:RELATES_TO]->()", "fact_embedding", "fact"),
)


def text_fuer_vektor(text):
    """Wie Graphiti: Zeilenumbrüche → Leerzeichen. Leere Texte bekommen keinen Vektor (None)."""
    if text is None:
        return None
    sauber = str(text).replace("\n", " ").strip()
    return sauber or None


def in_stuecken(liste, n):
    for i in range(0, len(liste), n):
        yield liste[i:i + n]


def vektor_pruefen(v, dim):
    """Fehler, wenn der Dienst etwas Unbrauchbares liefert (falsche Dimension, leer, nur Nullen)."""
    if not v or len(v) != dim:
        raise ValueError(f"Dimension {len(v) if v else 0} statt {dim} — Modell/--dim prüfen.")
    if not any(v):
        raise ValueError("Vektor besteht nur aus Nullen.")


def einbetten(url, texte):
    anfrage = urllib.request.Request(url, json.dumps({"model": "x", "input": texte}).encode(),
                                     {"Content-Type": "application/json"})
    daten = json.loads(urllib.request.urlopen(anfrage, timeout=300).read())["data"]
    return [d["embedding"] for d in sorted(daten, key=lambda d: d["index"])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    ap.add_argument("--ziel", required=True)
    ap.add_argument("--host", default="falkordb")
    ap.add_argument("--url", default="http://embedder:8080/v1/embeddings")
    ap.add_argument("--dim", type=int, default=384)
    ap.add_argument("--stueck", type=int, default=32)
    a = ap.parse_args()
    if a.ziel == a.graph:
        sys.exit("Ziel darf nicht der Quellgraph sein.")

    from falkordb import FalkorDB
    db = FalkorDB(host=a.host, port=6379)
    if a.graph not in db.list_graphs():
        sys.exit(f"Quellgraph {a.graph} gibt es nicht.")
    if a.ziel not in db.list_graphs():
        db.select_graph(a.graph).copy(a.ziel)          # GRAPH.COPY, die Quelle bleibt unberührt
    ziel = db.select_graph(a.ziel)

    fehlend = 0
    for etikett, muster, feld, quelle in ZIELE:
        zeilen = ziel.query(f"MATCH {muster} RETURN n.uuid, n.{quelle}").result_set
        mit_text = [(u, text_fuer_vektor(t)) for u, t in zeilen]
        ohne_text = [u for u, t in mit_text if not t]
        for stueck in in_stuecken([z for z in mit_text if z[1]], a.stueck):
            vektoren = einbetten(a.url, [t for _, t in stueck])
            for (uuid, _), v in zip(stueck, vektoren):
                vektor_pruefen(v, a.dim)
                ziel.query(f"MATCH {muster} WHERE n.uuid = $u SET n.{feld} = vecf32($v)", {"u": uuid, "v": v})
        gesamt = ziel.query(f"MATCH {muster} RETURN count(n)").result_set[0][0]
        mit = ziel.query(f"MATCH {muster} WHERE n.{feld} IS NOT NULL RETURN count(n)").result_set[0][0]
        print(f"{etikett}: {mit}/{gesamt} mit Vektor, {len(ohne_text)} ohne Text übersprungen")
        fehlend += gesamt - mit - len(ohne_text)
    if fehlend:
        sys.exit(f"{fehlend} Vektoren fehlen — NICHT umschalten.")
    print("Fertig. Quellgraph unverändert; Suchqualität prüfen (Task 4) vor dem Umschalten.")


if __name__ == "__main__":
    main()
