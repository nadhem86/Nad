"""Projection du knowledge graph vers le modèle en étoile analytique (tables dim_* / fct_*).

Les projections sont DÉRIVÉES : elles sont reconstruites à partir des faits courants validés du graphe,
avec les mêmes IRI canoniques. Les règles de calcul ligne par ligne (remises, retours, devises, fuseau,
coût à la date de commande) sont appliquées ici une seule fois, puis réutilisées par toutes les métriques.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .db import transaction, utcnow
from .kg import KnowledgeGraph


def local_date(ts_utc: str | None, tz: str) -> str | None:
    if not ts_utc:
        return None
    dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo(tz)).date().isoformat()


def fx_rate(conn, currency: str, day: str) -> float | None:
    if currency == "EUR":
        return 1.0
    row = conn.execute("SELECT rate_to_eur FROM fx_rate WHERE currency=? AND day=?", (currency, day)).fetchone()
    return row["rate_to_eur"] if row else None


def rebuild(kg: KnowledgeGraph, tz: str = "Europe/Paris") -> dict:
    conn = kg.conn
    counts = {}
    with transaction(conn):
        for t in ("dim_client", "dim_produit", "dim_entrepot", "bridge_produit_fournisseur",
                  "fct_commande", "fct_ligne", "fct_stock"):
            conn.execute(f"DELETE FROM {t}")

        for c in kg.entities("Client"):
            conn.execute("INSERT INTO dim_client VALUES (?,?,?,?,?)",
                         (c, kg.get(c, "numeroClient"), kg.get(c, "raisonSociale"), kg.get(c, "region"), kg.get(c, "secteur")))
        for p in kg.entities("Produit"):
            conn.execute("INSERT INTO dim_produit VALUES (?,?,?,?)",
                         (p, kg.get(p, "reference"), kg.get(p, "libelle"), kg.get(p, "categorie")))
            for v in kg.get_all(p, "fourniPar"):
                conn.execute("INSERT INTO bridge_produit_fournisseur VALUES (?,?,?)", (p, v, kg.get(v, "nom")))
        for w in kg.entities("Entrepot"):
            conn.execute("INSERT INTO dim_entrepot VALUES (?,?,?,?)",
                         (w, kg.get(w, "code"), kg.get(w, "nom"), kg.get(w, "region")))

        for o in kg.entities("Commande"):
            date_utc = kg.get(o, "dateCommande")
            day = local_date(date_utc, tz)
            devise = kg.get(o, "devise")
            statut = kg.get(o, "statut")
            client = kg.get(o, "passeePar")
            wh = kg.get(o, "prepareeDans")
            conn.execute("INSERT INTO fct_commande VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (o, kg.get(o, "numeroCommande"), client, wh, statut, date_utc, day, devise,
                          kg.get(o, "dateConfirmation"), kg.get(o, "dateExpedition"), kg.get(o, "dateLivraison"),
                          kg.get(o, "dateAnnulation")))
            rate = fx_rate(conn, devise, day) if devise and day else None
            for line, _ in kg.traverse(o, ["^ligneDe"]):
                q = kg.get(line, "quantite") or 0.0
                r = kg.get(line, "quantiteRetournee") or 0.0
                pu = kg.get(line, "prixUnitaire")
                dp = kg.get(line, "remisePct") or 0.0
                da = kg.get(line, "remiseMontant") or 0.0
                prod = kg.get(line, "porteSur")
                gross_net = max(0.0, q * pu * (1 - dp / 100.0) - da) if pu is not None else None
                # Retours : réduction au prorata du prix net unitaire.
                net = gross_net * (q - r) / q if gross_net is not None and q else None
                net_eur = round(net * rate, 6) if net is not None and rate is not None else None
                missing = None
                if net is None:
                    missing = "prix_unitaire_absent"
                elif rate is None:
                    missing = f"taux_change_absent:{devise}:{day}"
                cost, historical = kg.value_at(prod, "coutUnitaire", date_utc) if prod else (None, False)
                cost_eur = round(cost * (q - r), 6) if cost is not None else None
                cost_missing = None if cost is not None else "cout_unitaire_inconnu"
                if cost is not None and not historical:
                    cost_missing = "cout_courant_utilise_faute_d_historique"
                conn.execute(
                    "INSERT INTO fct_ligne VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (line, o, prod, client, wh, statut, day, devise, q, r, pu, dp, da, kg.get(line, "tauxTaxe"),
                     rate, net, net_eur, missing, cost, cost_eur, cost_missing))

        for s in kg.entities("ReleveStock"):
            conn.execute("INSERT INTO fct_stock VALUES (?,?,?,?,?)",
                         (s, kg.get(s, "dateReleve"), kg.get(s, "stockDe"), kg.get(s, "stockDans"), kg.get(s, "quantite")))

        conn.execute("INSERT OR REPLACE INTO projection_state VALUES (1, ?, ?)", (utcnow(), graph_signature(conn)))
        for t in ("dim_client", "fct_commande", "fct_ligne", "fct_stock"):
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return counts


def is_stale(conn) -> bool:
    """Désynchronisation détectable : des faits ont été écrits après la dernière projection."""
    st = conn.execute("SELECT fact_watermark FROM projection_state WHERE id=1").fetchone()
    return st is None or st["fact_watermark"] != graph_signature(conn)


def graph_signature(conn) -> str:
    """Empreinte de l'état du graphe : change à chaque insertion, remplacement ou suppression."""
    r = conn.execute("SELECT COALESCE(MAX(fact_id),0), COUNT(superseded_at) FROM fact").fetchone()
    d = conn.execute("SELECT COUNT(deleted_at) FROM entity").fetchone()[0]
    return f"{r[0]}:{r[1]}:{d}"
