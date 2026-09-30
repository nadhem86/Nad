"""Interface agents : définitions, résolution, relations, métriques, ambiguïtés et absence de données."""

from tests.conftest import iri_of


def test_define_concept_metric_and_state(tk):
    a = tk("emma.analyste")
    r = a.define("Commande")
    assert r["status"] == "ok" and r["data"]["id"] == "nad:Commande" and r["provenance"][0]["version"] == "1.0.0"
    r = a.define("Bon de commande")  # synonyme
    assert r["status"] == "ok" and r["data"]["id"] == "nad:Commande"
    r = a.define("chiffre_affaires")
    assert r["data"]["kind"] == "metric" and "devises" in r["data"]["rules"] and r["data"]["additivity"] == "additive"
    assert a.define("en préparation")["data"]["kind"] == "state"


def test_define_ambiguous_term_asks_clarification(tk):
    r = tk("emma.analyste").define("client")
    assert r["status"] == "ambiguous"
    assert set(r["clarification"]["options"]) == {"concept:Client", "concept:Contact"}


def test_define_unknown_term_is_not_invented(tk):
    r = tk("emma.analyste").define("EBITDA")
    assert r["status"] == "not_found" and r["data"] == {"term": "EBITDA"}


def test_resolve_entity_exact_ambiguous_phone(hub, tk):
    a = tk("alice.admin")
    assert a.resolve_entity("SO102")["data"]["entity"]["label"] == "SO102"
    assert a.resolve_entity("Martin")["status"] == "ambiguous"
    r = a.resolve_entity("06 12 34 56 78")
    assert r["status"] == "ambiguous"  # Contact (mobile) ET identité WhatsApp : même numéro, deux concepts
    assert {c["concept"] for c in r["data"]["candidates"]} == {"Contact", "IdentiteWhatsApp"}
    assert a.resolve_entity("06 12 34 56 78", "Contact")["data"]["entity"]["label"] == "Jeanne Martin"
    assert a.resolve_entity("Inexistant SARL")["status"] == "not_found"


def test_relations_with_provenance_and_inferred_flag(hub, tk):
    so102 = iri_of(hub, "SO102")
    r = tk("alice.admin").relations(so102, depth=1)
    preds = {e["predicate"] for e in r["data"]["edges"]}
    assert {"nad:passeePar", "nad:ligneDe", "nad:prepareeDans", "nad:contactReferent"} <= preds
    for e in r["data"]["edges"]:
        assert e["provenance"]["source"] and e["provenance"]["fact_id"]
    deep = tk("alice.admin").relations(so102, depth=2)
    assert any(e["inferred"] and e["provenance"]["rule_id"] == "R2" for e in deep["data"]["edges"])


def test_relations_hide_out_of_scope_nodes(hub, tk):
    pro5 = iri_of(hub, "PRO5")  # vendu à des clients Nord et Sud
    r = tk("bruno.commercial.nord").relations(pro5, depth=2)
    labels = {n["label"] for n in r["data"]["nodes"]}
    assert "SO105" not in labels and r["warnings"]


def test_impact_supplier(hub, tk):
    r = tk("alice.admin").impact(iri_of(hub, "VEN2"))  # Café Import
    clients = {i["label"] for i in r["data"]["impacts"]["clients_impactes"]["items"]}
    assert clients == {"Café du Port", "Hôtel Lumière"}


def test_compute_metric_envelope(hub, tk):
    r = tk("alice.admin").compute_metric(["chiffre_affaires"], group_by=["temps.trimestre"])
    rows = r["data"]["chiffre_affaires"]["rows"]
    assert [row["temps.trimestre"] for row in rows] == ["2026-T3"]
    assert r["provenance"][0]["model_version"] == "1.0.0" and "sql" in r["provenance"][0]
    assert any(w.get("reason", "").startswith("taux_change_absent") for w in r["warnings"])


def test_no_data_is_not_zero(tk):
    r = tk("alice.admin").compute_metric(["chiffre_affaires"], period_start="2025-01-01", period_end="2025-12-31")
    assert r["status"] == "no_data"
    assert r["data"]["chiffre_affaires"]["rows"] == [] or r["data"]["chiffre_affaires"]["rows"][0]["value"] is None


def test_invalid_metric_request_is_explicit_error(tk):
    r = tk("alice.admin").compute_metric(["ca_magique"])
    assert r["status"] == "forbidden" or r["error"]["code"] == "UNKNOWN_METRIC"
    r = tk("alice.admin").compute_metric(["chiffre_affaires"], group_by=["fournisseur.nom"])
    assert r["status"] == "error" and r["error"]["code"] == "FAN_OUT"


def test_list_and_execute_action_through_toolkit(hub, tk):
    so106 = iri_of(hub, "SO106")
    acts = {a["action"]: a for a in tk("agent.commercial.nord").list_actions(so106)["data"]["actions"]}
    assert acts["confirmer"]["allowed"]
    r = tk("agent.commercial.nord").execute_action("confirmer", so106, "agent-k1")
    assert r["status"] == "ok" and r["data"]["statut_apres"] == "confirmee"
    assert "SIMULÉE" in r["warnings"][0]
