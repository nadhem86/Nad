"""Journal d'audit chaîné : complétude et détection d'altération."""

from tests.conftest import iri_of


def test_actions_denials_reads_and_ingests_are_logged(hub, tk):
    so106 = iri_of(hub, "SO106")
    tk("bruno.commercial.nord").execute_action("confirmer", so106, "au1")
    tk("david.logisticien.lille").execute_action("confirmer", so106, "au2")
    kinds = {r[0] for r in hub.conn.execute("SELECT DISTINCT kind FROM audit_log")}
    assert {"ingest", "read", "action", "denied"} <= kinds
    ok, _ = hub.audit.verify()
    assert ok


def test_tampering_is_detected(hub, tk):
    tk("alice.admin").define("Commande")
    seq = hub.conn.execute("SELECT MIN(seq) FROM audit_log").fetchone()[0]
    hub.conn.execute("UPDATE audit_log SET outcome='falsifie' WHERE seq=?", (seq,))
    ok, broken = hub.audit.verify()
    assert not ok and broken == seq
