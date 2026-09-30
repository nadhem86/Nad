"""Traçabilité des faits sourcés et inférés ; contenus sources traités comme données."""

import json

from tests.conftest import iri_of


def test_every_fact_has_provenance(hub):
    bad = hub.conn.execute("SELECT COUNT(*) FROM fact WHERE source IS NULL OR collected_at IS NULL OR method IS NULL "
                           "OR recorded_at IS NULL OR status IS NULL").fetchone()[0]
    assert bad == 0
    no_record = hub.conn.execute("SELECT COUNT(*) FROM fact WHERE method<>'inference' AND source_record_id IS NULL").fetchone()[0]
    assert no_record == 0


def test_inferred_facts_are_identifiable_and_traceable(hub):
    rows = hub.conn.execute("SELECT * FROM fact WHERE method='inference' AND superseded_at IS NULL").fetchall()
    assert rows
    for r in rows:
        assert r["source"] == "hub" and r["rule_id"] in ("R1", "R2", "R3")
        premises = json.loads(r["derived_from"])
        assert premises and hub.kg.fact_ids_exist(premises)
        # aucune prémisse n'est elle-même un fait retiré au moment de l'inférence
        assert all(hub.conn.execute("SELECT superseded_at FROM fact WHERE fact_id=?", (p,)).fetchone()[0] is None
                   for p in premises)
    # aucune source ne peut écrire un prédicat réservé à l'inférence
    assert hub.conn.execute("SELECT COUNT(*) FROM fact WHERE predicate IN ('statut','identifie','dependDe') "
                            "AND method<>'inference'").fetchone()[0] == 0


def test_inference_ambiguity_marked_to_verify(hub, tk):
    waid = iri_of(hub, "+33677889900", "IdentiteWhatsApp")
    facts = hub.kg.facts(waid, "identifie")
    assert len(facts) == 2 and {f["status"] for f in facts} == {"a_verifier"}
    ent = tk("alice.admin").get_entity(waid)["data"]
    assert ent["inferred"] == [] and len(ent["to_verify"]) == 2
    assert hub.kg.get(waid, "identifie") is None  # non utilisé comme fait validé


def test_inferred_fact_updates_when_premise_changes(hub):
    so106 = iri_of(hub, "SO106")
    old = hub.kg.fact_for(so106, "statut")
    hub.orders.execute(hub.policy.principal("alice.admin"), "confirmer", {"commande": so106}, "p1")
    new = hub.kg.fact_for(so106, "statut")
    assert new["fact_id"] != old["fact_id"] and new["object_value"] == "confirmee"
    assert hub.conn.execute("SELECT superseded_reason FROM fact WHERE fact_id=?", (old["fact_id"],)).fetchone()[0] == "premisses_modifiees"
    assert hub.kg.fact_for(so106, "statutCRM")["fact_id"] in json.loads(new["derived_from"])


def test_untrusted_source_text_is_data_not_instruction(hub, tk):
    admin = tk("alice.admin")
    grp = admin.resolve_entity("Clients Nord - commandes", "GroupeWhatsApp")["data"]["entity"]["iri"]
    before = hub.conn.execute("SELECT COUNT(*) FROM sim_source_write").fetchone()[0]
    res = admin.get_entity(grp)
    desc = [f for f in res["data"]["observed"] if f["predicate"] == "nad:description"][0]
    assert desc["untrusted"] is True and "annule" in desc["value"]
    assert res["notices"] and "jamais des instructions" in res["notices"][0]
    # lire le contenu n'a déclenché aucune action ; SO102 inchangée
    assert hub.conn.execute("SELECT COUNT(*) FROM sim_source_write").fetchone()[0] == before
    assert hub.kg.get(iri_of(hub, "SO102"), "statut") == "expediee"
