"""Validité des entités et relations ; cohérence des identifiants entre les couches."""

import re

import yaml
from rdflib import Graph

from hub.config import Settings
from hub.glossary import load_glossary
from hub.rdf import build_ontology, build_shapes, validate
from hub.semantic import SemanticModel


def test_generated_ttl_in_sync_with_glossary():
    s = Settings()
    g = load_glossary(s)
    for name, builder in (("ontology.ttl", build_ontology), ("shapes.ttl", build_shapes)):
        on_disk = Graph().parse(s.glossary_path.parent / name)
        assert len(on_disk) == len(builder(g)), f"{name} obsolète : lancer scripts/build_ontology.py"


def test_glossary_has_required_concepts_and_metadata():
    g = load_glossary()
    for c in ("Client", "Commande", "LigneCommande", "Produit", "Entrepot", "Fournisseur"):
        concept = g.concept(c)
        assert concept.definition and concept.owner and concept.source_ids
    assert g.member("LigneCommande", "ligneDe").min == 1 and g.member("LigneCommande", "ligneDe").max == 1
    assert g.member("Produit", "fourniPar").max is None  # N-N


def test_demo_graph_conforms_to_shacl(hub):
    ok, violations = validate(hub.kg)
    assert ok, violations


def test_shacl_detects_invalid_line(hub):
    line = hub.kg.entities("LigneCommande")[0]
    hub.conn.execute("UPDATE fact SET object_value='-3' WHERE subject=? AND predicate='quantite' AND superseded_at IS NULL", (line,))
    hub.conn.execute("UPDATE fact SET superseded_at='x' WHERE subject=? AND predicate='porteSur'", (line,))
    ok, violations = validate(hub.kg)
    assert not ok
    paths = {(v["focus"], v["path"].split("#")[-1]) for v in violations}
    assert (line, "quantite") in paths and (line, "porteSur") in paths


def test_every_emitted_predicate_is_in_glossary(hub):
    preds = {r[0] for r in hub.conn.execute("SELECT DISTINCT predicate FROM fact")}
    assert preds <= load_glossary().predicates()


def test_identifiers_consistent_across_layers(hub):
    """Les clés des projections analytiques sont des IRI du graphe, du bon concept."""
    checks = {"dim_client": ("client_iri", "Client"), "dim_produit": ("produit_iri", "Produit"),
              "dim_entrepot": ("entrepot_iri", "Entrepot"), "fct_commande": ("commande_iri", "Commande"),
              "fct_ligne": ("ligne_iri", "LigneCommande"), "fct_stock": ("releve_iri", "ReleveStock")}
    for table, (col, concept) in checks.items():
        rows = hub.conn.execute(f"SELECT {col} FROM {table}").fetchall()
        assert rows
        for (iri,) in rows:
            assert re.fullmatch(r"urn:nad:[a-z]+:[0-9a-f]{12}", iri)
            assert hub.kg.concept_of(iri) == concept
    # chaque IRI a au moins une correspondance vers un identifiant source
    orphans = hub.conn.execute("SELECT COUNT(*) FROM entity e WHERE NOT EXISTS "
                               "(SELECT 1 FROM source_identity s WHERE s.iri=e.iri)").fetchone()[0]
    assert orphans == 0


def test_semantic_model_and_lifecycle_reference_glossary():
    s = Settings()
    g = load_glossary(s)
    model = SemanticModel(s.model_path)
    for dim in model.dimensions.values():
        if "concept" in dim:
            ref = dim["concept"].removeprefix("nad:")
            concept, _, prop = ref.partition(".")
            assert concept in g.concepts and (not prop or g.member(concept, prop))
    life = yaml.safe_load(s.lifecycle_path.read_text())
    assert set(life["states"]) == set(g.member("Commande", "statut").values)
    for t in life["transitions"]:
        assert t["from"] in life["states"] and t["to"] in life["states"]
