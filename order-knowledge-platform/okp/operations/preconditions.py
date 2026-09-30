"""Préconditions métier nommées, référencées par models/operations.yaml.

Chaque fonction reçoit la connexion, le contexte de commande (état synchronisé) et les
paramètres validés ; elle renvoie (satisfaite, explication)."""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

Check = Callable[[sqlite3.Connection, dict[str, Any], dict[str, Any]], tuple[bool, str]]


def has_lines(conn, ctx, params):
    n = len(ctx["lines"])
    return n >= 1, f"{n} ligne(s) active(s)"


def positive_quantities(conn, ctx, params):
    bad = [l["line_no"] for l in ctx["lines"] if l["quantity"] < 1]
    return not bad, "toutes les quantités sont ≥ 1" if not bad else f"lignes avec quantité < 1 : {bad}"


def customer_active(conn, ctx, params):
    return ctx["customer_status"] == "active", f"statut client : {ctx['customer_status']}"


def products_active(conn, ctx, params):
    inactive = [l["sku"] for l in ctx["lines"] if not l["product_active"]]
    return not inactive, "tous les produits sont actifs" if not inactive else f"produits inactifs : {inactive}"


def not_shipped(conn, ctx, params):
    return ctx["status"] not in ("shipped", "delivered"), f"statut : {ctx['status']}"


def warehouse_active(conn, ctx, params):
    wh = params.get("_warehouse")
    if wh is None:
        return False, "entrepôt introuvable"
    return bool(wh["active"]), f"entrepôt {wh['code']} {'actif' if wh['active'] else 'inactif'}"


def stock_sufficient(conn, ctx, params):
    wh = params.get("_warehouse")
    if wh is None:
        return False, "entrepôt introuvable"
    need: dict[str, int] = {}
    for l in ctx["lines"]:
        need[l["product_id"]] = need.get(l["product_id"], 0) + l["quantity"]
    short = []
    for pid, qty in need.items():
        row = conn.execute(
            "SELECT on_hand FROM inventory_snapshot WHERE warehouse_id=? AND product_id=? "
            "ORDER BY snapshot_date DESC LIMIT 1", (wh["warehouse_id"], pid)).fetchone()
        on_hand = row["on_hand"] if row else 0
        if on_hand < qty:
            sku = next(l["sku"] for l in ctx["lines"] if l["product_id"] == pid)
            short.append(f"{sku} : besoin {qty}, stock {on_hand}")
    return not short, "stock suffisant au dernier instantané" if not short else "stock insuffisant — " + "; ".join(short)


def warehouse_assigned(conn, ctx, params):
    return ctx["warehouse_id"] is not None, f"entrepôt affecté : {ctx['warehouse_code'] or 'aucun'}"


REGISTRY: dict[str, Check] = {f.__name__: f for f in (
    has_lines, positive_quantities, customer_active, products_active, not_shipped,
    warehouse_active, stock_sufficient, warehouse_assigned)}
