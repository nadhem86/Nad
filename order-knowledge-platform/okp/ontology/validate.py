"""Export du knowledge graph en RDF (TriG, un graphe nommé par système source et un pour
les inférences) et validation SHACL (pyshacl) des faits courants."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

from pyshacl import validate as shacl_validate
from rdflib import Dataset, Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, SH, XSD

from okp.ontology.build import XSD_TYPES, build_ontology, build_shapes, namespaces
from okp.registry import Glossary


def _literal(value, ptype: str) -> Literal:
    if ptype == "decimal":
        return Literal(Decimal(str(value)), datatype=XSD.decimal)
    if ptype == "integer":
        return Literal(int(value), datatype=XSD.integer)
    if ptype == "boolean":
        return Literal(bool(value), datatype=XSD.boolean)
    return Literal(str(value), datatype=XSD_TYPES[ptype])


def export_dataset(conn: sqlite3.Connection, g: Glossary, include_inferred: bool = True) -> Dataset:
    OKP, INST = namespaces(g)
    ds = Dataset()
    ds.bind("okp", OKP), ds.bind("inst", INST)
    ptypes = {g.predicate(c, p): pd.type for c, cd in g.concepts.items() for p, pd in cd.properties.items()}
    graphs: dict[str, Graph] = {}

    def graph_for(name: str) -> Graph:
        if name not in graphs:
            graphs[name] = ds.graph(URIRef(f"{g.instance_namespace}graph/{name}"))
        return graphs[name]

    for e in conn.execute("SELECT canonical_id, concept, label FROM entity WHERE deleted_at IS NULL"):
        gr = graph_for("entities")
        gr.add((INST[e["canonical_id"]], RDF.type, OKP[e["concept"]]))
        gr.add((INST[e["canonical_id"]], RDFS.label, Literal(e["label"])))
    where = "superseded_at IS NULL AND retracted_at IS NULL AND validation_status <> 'rejected'"
    if not include_inferred:
        where += " AND method <> 'inference'"
    for f in conn.execute(f"SELECT * FROM fact WHERE {where}"):
        gr = graph_for("inferred" if f["method"] == "inference" else f["source_system"])
        s, p = INST[f["subject"]], OKP[f["predicate"]]
        if f["object_id"]:
            gr.add((s, p, INST[f["object_id"]]))
        else:
            value = json.loads(f["object_value"])
            gr.add((s, p, _literal(value, ptypes.get(f["predicate"], "string"))))
    return ds


def validate_graph(data: Graph, g: Glossary) -> dict:
    conforms, results_graph, text = shacl_validate(
        data, shacl_graph=build_shapes(g), ont_graph=build_ontology(g), inference="none", advanced=True,
        allow_warnings=True)
    violations = []
    for r in results_graph.subjects(RDF.type, SH.ValidationResult):
        violations.append({
            "focus": str(results_graph.value(r, SH.focusNode)),
            "path": str(results_graph.value(r, SH.resultPath) or ""),
            "severity": str(results_graph.value(r, SH.resultSeverity)).rsplit("#", 1)[-1],
            "message": str(results_graph.value(r, SH.resultMessage) or ""),
        })
    return {"conforms": conforms, "violations": violations}


def validate_kg(conn: sqlite3.Connection, g: Glossary) -> dict:
    ds = export_dataset(conn, g, include_inferred=False)
    merged = Graph()
    for ctx in ds.graphs():
        for t in ctx:
            merged.add(t)
    report = validate_graph(merged, g)
    report["triples"] = len(merged)
    return report
