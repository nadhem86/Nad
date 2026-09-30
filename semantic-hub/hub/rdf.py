"""Représentation RDF : génération de l'ontologie OWL et des formes SHACL depuis le glossaire, export du
knowledge graph (TriG, un graphe nommé par source) et validation SHACL.

SHACL valide la STRUCTURE : types, cardinalités, énumérations, bornes et références. Les règles
dynamiques (transitions d'état, permissions, stock) sont appliquées par la couche opérationnelle : un
moteur de raisonnement ne les valide pas.
"""

from __future__ import annotations

from rdflib import BNode, Dataset, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS, SKOS, XSD

from .glossary import Glossary

SH = Namespace("http://www.w3.org/ns/shacl#")
SCHEMA = Namespace("https://schema.org/")
XSD_MAP = {"string": XSD.string, "decimal": XSD.decimal, "integer": XSD.integer, "boolean": XSD.boolean,
           "dateTime": XSD.dateTime, "date": XSD.date, "enum": XSD.string}


def _ns(g: Glossary) -> Namespace:
    return Namespace(g.namespace)


def build_ontology(g: Glossary) -> Graph:
    NAD = _ns(g)
    out = Graph()
    out.bind("nad", NAD); out.bind("owl", OWL); out.bind("skos", SKOS); out.bind("schema", SCHEMA)
    onto = URIRef(g.namespace.rstrip("#"))
    out.add((onto, RDF.type, OWL.Ontology))
    out.add((onto, OWL.versionInfo, Literal(g.version)))
    out.add((onto, RDFS.comment, Literal("Généré depuis ontology/glossary.yaml — ne pas éditer à la main.", lang="fr")))
    domains: dict[str, list[str]] = {}
    for c in g.concepts.values():
        cls = NAD[c.name]
        out.add((cls, RDF.type, OWL.Class))
        out.add((cls, RDFS.label, Literal(c.label, lang="fr")))
        out.add((cls, SKOS.definition, Literal(c.definition, lang="fr")))
        out.add((cls, NAD.responsableMetier, Literal(c.owner)))
        for s in c.synonyms:
            out.add((cls, SKOS.altLabel, Literal(s, lang="fr")))
        for a in c.ambiguities:
            out.add((cls, SKOS.scopeNote, Literal(f"{a['service']} : {a['meaning']}", lang="fr")))
        for name in list(c.properties) + list(c.relations):
            domains.setdefault(name, []).append(c.name)
    for c in g.concepts.values():
        for p in c.properties.values():
            prop = NAD[p.name]
            out.add((prop, RDF.type, OWL.DatatypeProperty))
            out.add((prop, RDFS.range, XSD_MAP[p.type]))
            if p.definition:
                out.add((prop, SKOS.definition, Literal(p.definition, lang="fr")))
            if p.pii:
                out.add((prop, NAD.donneePersonnelle, Literal(True)))
            if p.untrusted:
                out.add((prop, NAD.contenuNonFiable, Literal(True)))
            if p.inferred_only:
                out.add((prop, NAD.inferenceUniquement, Literal(True)))
        for r in c.relations.values():
            prop = NAD[r.name]
            out.add((prop, RDF.type, OWL.ObjectProperty))
            out.add((prop, RDFS.range, NAD[r.target]))
            if r.definition:
                out.add((prop, SKOS.definition, Literal(r.definition, lang="fr")))
            if r.inferred_only:
                out.add((prop, NAD.inferenceUniquement, Literal(True)))
            if r.max == 1:
                out.add((prop, RDF.type, OWL.FunctionalProperty))
    for name, ds in domains.items():
        if len(ds) == 1:
            out.add((NAD[name], RDFS.domain, NAD[ds[0]]))
        else:  # propriété partagée : pas de rdfs:domain (qui provoquerait des inférences de type erronées)
            for d in ds:
                out.add((NAD[name], SCHEMA.domainIncludes, NAD[d]))
    return out


def build_shapes(g: Glossary) -> Graph:
    NAD = _ns(g)
    out = Graph()
    out.bind("nad", NAD); out.bind("sh", SH)
    for c in g.concepts.values():
        shape = NAD[f"{c.name}Shape"]
        out.add((shape, RDF.type, SH.NodeShape))
        out.add((shape, SH.targetClass, NAD[c.name]))
        for p in c.properties.values():
            ps = BNode(f"{c.name}_{p.name}")
            out.add((shape, SH.property, ps))
            out.add((ps, SH.path, NAD[p.name]))
            out.add((ps, SH.datatype, XSD_MAP[p.type]))
            out.add((ps, SH.maxCount, Literal(1)))
            if p.required:
                out.add((ps, SH.minCount, Literal(1)))
            if p.values:
                lst = BNode(f"{c.name}_{p.name}_in")
                Collection(out, lst, [Literal(v, datatype=XSD.string) for v in p.values])
                out.add((ps, SH["in"], lst))
            for k, shk in (("pattern", SH.pattern), ("min_inclusive", SH.minInclusive),
                           ("min_exclusive", SH.minExclusive), ("max_inclusive", SH.maxInclusive)):
                if k in p.constraints:
                    v = p.constraints[k]
                    out.add((ps, shk, Literal(v) if k == "pattern" else Literal(str(v), datatype=XSD.decimal)))
        for r in c.relations.values():
            ps = BNode(f"{c.name}_{r.name}")
            out.add((shape, SH.property, ps))
            out.add((ps, SH.path, NAD[r.name]))
            out.add((ps, SH["class"], NAD[r.target]))
            if r.min:
                out.add((ps, SH.minCount, Literal(r.min)))
            if r.max is not None:
                out.add((ps, SH.maxCount, Literal(r.max)))
    # contrainte inter-propriétés : on ne retourne pas plus que ce qui a été commandé
    ps = BNode("LigneCommande_retour_borne")
    out.add((NAD.LigneCommandeShape, SH.property, ps))
    out.add((ps, SH.path, NAD.quantiteRetournee))
    out.add((ps, SH.lessThanOrEquals, NAD.quantite))
    return out


def export_graph(kg, statuses: tuple[str, ...] = ("valide",)) -> Dataset:
    """Export des faits COURANTS en TriG : un graphe nommé par source (vtiger, wms, whatsapp, hub = inférences)."""
    g = kg.g
    NAD = _ns(g)
    ds = Dataset()
    ds.bind("nad", NAD)
    types = ds.graph(URIRef("urn:nad:graph:types"))
    for e in kg.conn.execute("SELECT iri, concept FROM entity WHERE deleted_at IS NULL"):
        types.add((URIRef(e["iri"]), RDF.type, NAD[e["concept"]]))
    q = (f"SELECT f.* FROM fact f JOIN entity e ON e.iri=f.subject WHERE f.superseded_at IS NULL AND e.deleted_at IS NULL "
         f"AND f.status IN ({','.join('?' * len(statuses))})")
    for r in kg.conn.execute(q, statuses):
        graph = ds.graph(URIRef(f"urn:nad:graph:{r['source']}"))
        if r["object_iri"] is not None:
            obj = URIRef(r["object_iri"])
        else:
            obj = Literal(r["object_value"], datatype=XSD_MAP.get(r["datatype"], XSD.string))
        graph.add((URIRef(r["subject"]), NAD[r["predicate"]], obj))
    return ds


def validate(kg) -> tuple[bool, list[dict]]:
    from pyshacl import validate as shacl_validate

    data = Graph()
    for t in export_graph(kg).quads():
        data.add(t[:3])
    conforms, report_graph, _ = shacl_validate(data, shacl_graph=build_shapes(kg.g), ont_graph=build_ontology(kg.g),
                                               inference="none", advanced=True)
    results = []
    for res in report_graph.subjects(RDF.type, SH.ValidationResult):
        results.append({
            "focus": str(report_graph.value(res, SH.focusNode)),
            "path": str(report_graph.value(res, SH.resultPath)),
            "message": str(report_graph.value(res, SH.resultMessage)),
            "value": str(report_graph.value(res, SH.value)) if report_graph.value(res, SH.value) is not None else None,
        })
    return conforms, sorted(results, key=lambda r: (r["focus"], r["path"]))


def write_artifacts(g: Glossary, directory) -> None:
    build_ontology(g).serialize(directory / "ontology.ttl", format="turtle")
    build_shapes(g).serialize(directory / "shapes.ttl", format="turtle")
