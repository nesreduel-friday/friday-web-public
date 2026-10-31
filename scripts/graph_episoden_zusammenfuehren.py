"""Führt doppelte Episoden gleichen Namens in EINER zusammen — ohne Fakten zu verlieren.

Graphitis eigenes `delete_episode` würde auch die Kanten (Fakten) löschen, die ein Duplikat angelegt hat. Hier bleiben alle
Fakten und Knoten: Kanten, die auf ein Duplikat zeigen, zeigen danach auf die behaltene (älteste) Episode; Knoten, die nur
ein Duplikat erwähnte, bekommen eine MENTIONS-Verbindung von der behaltenen Episode; erst dann werden die Duplikate
gelöscht. Vorher IMMER sichern (GRAPH.COPY) — der Aufruf prüft das nicht.

Aufruf im Graphiti-Image:
  docker run --rm --network friday-web_default -v $PWD/scripts:/s:ro zepai/knowledge-graph-mcp:1.1.0-graphiti-0.30.1-standalone \\
    /app/mcp/.venv/bin/python /s/graph_episoden_zusammenfuehren.py --graph eike --name "whatsapp-9-kontakt_r liebethal" [--anwenden]
Ohne --anwenden wird nur gezählt (Trockenlauf).
"""
import argparse
import sys
import uuid
from datetime import datetime, timezone


def neue_quellen(episoden, dups, keep):
    """Die Episodenliste einer Kante nach dem Zusammenführen: Duplikate werden zur behaltenen Episode, ohne doppelte
    Einträge, Reihenfolge der übrigen bleibt."""
    ergebnis = []
    for e in episoden or []:
        e = keep if e in dups else e
        if e not in ergebnis:
            ergebnis.append(e)
    return ergebnis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--host", default="falkordb")
    ap.add_argument("--anwenden", action="store_true")
    a = ap.parse_args()

    from falkordb import FalkorDB
    g = FalkorDB(host=a.host, port=6379).select_graph(a.graph)
    eps = g.query("MATCH (e:Episodic {name: $n}) RETURN e.uuid ORDER BY e.created_at", {"n": a.name}).result_set
    if len(eps) < 2:
        print("Keine Duplikate.")
        return
    keep, dups = eps[0][0], [e[0] for e in eps[1:]]
    dupset = set(dups)
    print(f"{len(eps)} Episoden '{a.name}': behalte {keep[:8]}, {len(dups)} Duplikate")

    kanten = g.query("MATCH ()-[r:RELATES_TO]->() RETURN r.uuid, r.episodes").result_set
    zu_aendern = [(u, neue_quellen(e, dupset, keep)) for u, e in kanten if e and any(x in dupset for x in e)]
    erwaehnt_keep = {r[0] for r in g.query("MATCH (:Episodic {uuid: $k})-[:MENTIONS]->(n) RETURN n.uuid", {"k": keep}).result_set}
    erwaehnt_dup = {r[0] for r in g.query("MATCH (e:Episodic)-[:MENTIONS]->(n) WHERE e.uuid IN $d RETURN DISTINCT n.uuid",
                                          {"d": dups}).result_set}
    fehlend = sorted(erwaehnt_dup - erwaehnt_keep)
    print(f"Kanten, die umgehängt werden: {len(zu_aendern)}; Knoten, die die behaltene Episode zusätzlich erwähnt: {len(fehlend)}")
    if not a.anwenden:
        print("Trockenlauf — nichts geändert (--anwenden zum Ausführen).")
        return

    jetzt = datetime.now(timezone.utc).isoformat()
    for u, quellen in zu_aendern:
        g.query("MATCH ()-[r:RELATES_TO {uuid: $u}]->() SET r.episodes = $q", {"u": u, "q": quellen})
    for n in fehlend:
        g.query("MATCH (e:Episodic {uuid: $k}), (n:Entity {uuid: $n}) "
                "CREATE (e)-[:MENTIONS {uuid: $u, group_id: $g, created_at: $t}]->(n)",
                {"k": keep, "n": n, "u": str(uuid.uuid4()), "g": a.graph, "t": jetzt})
    for d in dups:
        g.query("MATCH (e:Episodic {uuid: $d}) DETACH DELETE e", {"d": d})
    rest = g.query("MATCH (e:Episodic {name: $n}) RETURN count(e)", {"n": a.name}).result_set[0][0]
    print(f"Fertig: {rest} Episode(n) '{a.name}' übrig.")
    if rest != 1:
        sys.exit(1)


if __name__ == "__main__":
    main()
