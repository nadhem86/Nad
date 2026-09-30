"""Génération de l'ontologie OWL et des formes SHACL à partir du glossaire.

- ontology.ttl : classes (owl:Class), propriétés de données et d'objets, labels, définitions
  (skos:definition), synonymes (skos:altLabel), responsable (dcterms:creator), inverses.
  C'est la description des CONCEPTS (niveau classe). Aucune contrainte de validation ici :
  OWL est en monde ouvert et un raisonneur n'y détecterait pas une donnée manquante.
- shapes.ttl : contraintes en monde fermé (SHACL) : types, obligatoires, cardinalités,
  énumérations, motifs, bornes et règles conditionnelles SPARQL du glossaire.
"""

from __future__ import annotations

from pathlib import Path

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SH, SKOS, XSD

from okp.registry import Glossary

XSD_TYPES = {
    "string": XSD.string, "enum": XSD.string, "integer": XSD.integer, "decimal": XSD.decimal,
    "boolean": XSD.boolean, "date": XSD.date, "datetime": XSD.dateTime,
}


def namespaces(g: Glossary) -> tuple[Namespace, Namespace]:
    return Namespace(g.namespace), Namespace(g.instance_namespace)


def build_ontology(g: Glossary) -> Graph:
    OKP, _ = namespaces(g)
    gr = Graph()
    gr.bind("okp", OKP), gr.bind("skos", SKOS), gr.bind("owl", OWL), gr.bind("dcterms", DCTERMS)
    onto = URIRef(g.namespace.rstrip("#"))
    gr.add((onto, RDF.type, OWL.Ontology))
    gr.add((onto, OWL.versionInfo, Literal(g.version)))
    for name, c in g.concepts.items():
        cls = OKP[name]
        gr.add((cls, RDF.type, OWL.Class))
        gr.add((cls, RDFS.label, Literal(c.label, lang="fr")))
        gr.add((cls, RDFS.label, Literal(name, lang="en")))
        gr.add((cls, SKOS.definition, Literal(c.definition, lang="fr")))
        gr.add((cls, DCTERMS.creator, Literal(c.owner)))
        for syn in c.synonyms:
            gr.add((cls, SKOS.altLabel, Literal(syn, lang="fr")))
        for amb in c.ambiguities:
            gr.add((cls, SKOS.scopeNote, Literal(f"[{amb.service}] {amb.meaning} → {amb.resolution}", lang="fr")))
        for pname, p in c.properties.items():
            prop = OKP[g.predicate(name, pname)]
            gr.add((prop, RDF.type, OWL.DatatypeProperty))
            gr.add((prop, RDFS.domain, cls))
            gr.add((prop, RDFS.range, XSD_TYPES[p.type]))
            gr.add((prop, RDFS.label, Literal(pname)))
            gr.add((prop, SKOS.definition, Literal(p.definition, lang="fr")))
            gr.add((prop, OKP.sensitivity, Literal(p.sensitivity)))
    for name, r in g.relations.items():
        prop = OKP[name]
        gr.add((prop, RDF.type, OWL.ObjectProperty))
        gr.add((prop, RDFS.domain, OKP[r.domain]))
        gr.add((prop, RDFS.range, OKP[r.range]))
        gr.add((prop, RDFS.label, Literal(r.label, lang="fr")))
        gr.add((prop, SKOS.definition, Literal(r.definition, lang="fr")))
        if r.max == 1:
            gr.add((prop, RDF.type, OWL.FunctionalProperty))
    for name, r in g.inferred_relations.items():
        prop = OKP[name]
        gr.add((prop, RDF.type, OWL.ObjectProperty if r.range != "literal" else OWL.DatatypeProperty))
        gr.add((prop, RDFS.domain, OKP[r.domain]))
        if r.range != "literal":
            gr.add((prop, RDFS.range, OKP[r.range]))
        gr.add((prop, RDFS.label, Literal(r.label, lang="fr")))
        gr.add((prop, SKOS.definition, Literal(f"Relation inférée ({r.rule_id}) : {r.rule}", lang="fr")))
    return gr


def build_shapes(g: Glossary) -> Graph:
    OKP, _ = namespaces(g)
    SHAPES = Namespace(g.namespace.rstrip("#") + "/shapes#")
    gr = Graph()
    gr.bind("sh", SH), gr.bind("okp", OKP), gr.bind("okps", SHAPES)
    node_shapes = {}
    for name, c in g.concepts.items():
        ns = SHAPES[f"{name}Shape"]
        node_shapes[name] = ns
        gr.add((ns, RDF.type, SH.NodeShape))
        gr.add((ns, SH.targetClass, OKP[name]))
        for pname, p in c.properties.items():
            ps = BNode()
            gr.add((ns, SH.property, ps))
            gr.add((ps, SH.path, OKP[g.predicate(name, pname)]))
            gr.add((ps, SH.datatype, XSD_TYPES[p.type]))
            gr.add((ps, SH.maxCount, Literal(1)))
            if p.required:
                gr.add((ps, SH.minCount, Literal(1)))
            if p.values:
                lst = BNode()
                Collection(gr, lst, [Literal(v, datatype=XSD.string) for v in p.values])
                gr.add((ps, SH["in"], lst))
            if p.pattern:
                gr.add((ps, SH.pattern, Literal(p.pattern)))
            if p.min is not None:
                gr.add((ps, SH.minInclusive, Literal(p.min)))
            if p.max is not None:
                gr.add((ps, SH.maxInclusive, Literal(p.max)))
    for name, r in g.relations.items():
        ps = BNode()
        gr.add((node_shapes[r.domain], SH.property, ps))
        gr.add((ps, SH.path, OKP[name]))
        gr.add((ps, SH["class"], OKP[r.range]))
        gr.add((ps, SH.nodeKind, SH.IRI))
        if r.min:
            gr.add((ps, SH.minCount, Literal(r.min)))
        if r.max is not None:
            gr.add((ps, SH.maxCount, Literal(r.max)))
        if r.inverse_min or r.inverse_max is not None:
            inv = BNode()
            path = BNode()
            gr.add((node_shapes[r.range], SH.property, inv))
            gr.add((inv, SH.path, path))
            gr.add((path, SH.inversePath, OKP[name]))
            if r.inverse_min:
                gr.add((inv, SH.minCount, Literal(r.inverse_min)))
            if r.inverse_max is not None:
                gr.add((inv, SH.maxCount, Literal(r.inverse_max)))
    for c in g.constraints:
        if not c.sparql:
            continue
        sc = BNode()
        gr.add((node_shapes[c.target], SH.sparql, sc))
        gr.add((sc, SH.select, Literal(f"PREFIX okp: <{g.namespace}>\n{c.sparql}")))
        gr.add((sc, SH.message, Literal(f"{c.id} : {c.description}", lang="fr")))
        gr.add((sc, SH.severity, SH.Violation if c.severity == "violation" else SH.Warning))
    return gr


def write_artifacts(g: Glossary, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for fname, graph in (("ontology.ttl", build_ontology(g)), ("shapes.ttl", build_shapes(g))):
        header = (f"# FICHIER GÉNÉRÉ depuis models/glossary.yaml (version {g.version}) par `okp build-ontology`.\n"
                  "# Ne pas modifier à la main.\n")
        path = out_dir / fname
        path.write_text(header + graph.serialize(format="turtle"), encoding="utf-8")
        paths.append(path)
    return paths
