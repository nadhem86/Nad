"""Cohérence des modèles versionnés entre couches (glossaire, core, sémantique, opérations)."""

from rdflib.compare import isomorphic
from rdflib import Graph

from okp.config import PROJECT_ROOT
from okp.ontology.build import build_ontology, build_shapes
from okp.operations import preconditions
from okp.operations.actions import ERP_EFFECTS
from okp.registry import Registry

REG = Registry(PROJECT_ROOT / "models")


def test_models_load_with_versions():
    assert set(REG.versions) == {"glossary", "semantic_model", "operations", "policies"}
    assert all(v.count(".") == 2 for v in REG.versions.values())


def test_required_concepts_present():
    assert {"Customer", "Order", "OrderLine", "Product", "Warehouse", "Supplier"} <= set(REG.glossary.concepts)
    for name, c in REG.glossary.concepts.items():
        assert c.definition and c.owner and c.synonyms and c.source_mappings, name


def test_core_tables_have_every_glossary_property(pf):
    for name, c in REG.glossary.concepts.items():
        cols = {r["name"] for r in pf.conn.execute(f"PRAGMA table_info({c.table})")}
        assert set(c.properties) <= cols, (name, set(c.properties) - cols)


def test_relation_projections_exist_in_core(pf):
    for name, r in REG.glossary.relations.items():
        cols = {row["name"] for row in pf.conn.execute(f"PRAGMA table_info({r.projection['table']})")}
        needed = {r.projection["subject_column"], r.projection["object_column"]} if r.projection.get("link") \
            else {r.projection["column"]}
        assert needed <= cols, name


def test_source_authority_only_covers_declared_properties():
    for name, c in REG.glossary.concepts.items():
        for sm in c.source_mappings:
            assert set(sm.authority_for) <= set(c.properties), (name, sm.system)


def test_semantic_dimensions_reference_glossary_and_views_exist(pf):
    views = {r[0] for r in pf.conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    for d in REG.semantic.dimensions.values():
        if d.table:
            assert d.table in views
    for f in REG.semantic.facts.values():
        assert f.view in views


def test_every_metric_compiles_and_runs(pf):
    for name in REG.semantic.metrics:
        assert pf.metrics.query(name)["status"] in ("ok", "no_data")


def test_operations_consistent_with_code():
    assert {c for a in REG.operations.actions.values() for c in a.preconditions} <= set(preconditions.REGISTRY)
    assert set(ERP_EFFECTS) == set(REG.operations.actions)
    assert set(REG.glossary.concepts["Order"].properties["status"].values) == set(REG.operations.states)


def test_policies_reference_existing_metrics_and_actions():
    for role in REG.policies.roles.values():
        for perm in role.permissions:
            fam, _, target = perm.partition(":")
            if fam == "metric.read" and target != "*":
                assert target in REG.semantic.metrics, perm
            if fam == "action.execute":
                assert target in REG.operations.actions, perm
            if fam == "entity.read" and target != "*":
                assert target in REG.glossary.concepts, perm


def test_generated_ontology_is_up_to_date():
    for fname, graph in (("ontology.ttl", build_ontology(REG.glossary)), ("shapes.ttl", build_shapes(REG.glossary))):
        committed = Graph().parse(PROJECT_ROOT / "generated" / fname, format="turtle")
        assert isomorphic(committed, graph), f"{fname} obsolète : lancer `python -m okp build-ontology`"
