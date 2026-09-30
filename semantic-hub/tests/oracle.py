"""Oracle INDÉPENDANT : recalcule les métriques directement depuis les fichiers sources bruts, sans utiliser
le code du hub. Sert à vérifier l'exactitude des métriques et l'absence de doubles comptages."""

import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DEMO = Path(__file__).resolve().parent.parent / "data" / "demo"
STATUS = {"Created": "brouillon", "Approved": "confirmee", "Delivered": "livree", "Cancelled": "annulee"}
VALID = {"confirmee", "en_preparation", "expediee", "livree"}


def load(snapshot="snapshot_2026-09-30.json", delta=None):
    snap = json.loads((DEMO / "vtiger" / snapshot).read_text())
    orders = {o["id"]: o for o in snap["SalesOrder"]}
    products = {p["id"]: p for p in snap["Products"]}
    if delta:
        d = json.loads((DEMO / "vtiger" / delta).read_text())
        orders.update({o["id"]: o for o in d["updated"].get("SalesOrder", [])})
    events = list(csv.DictReader(open(DEMO / "wms" / "events.csv")))
    fx = {(r["currency"], r["date"]): float(r["rate_to_eur"]) for r in csv.DictReader(open(DEMO / "reference" / "fx_rates.csv"))}
    return snap, orders, products, events, fx


def order_status(o, events):
    crm = STATUS[o["sostatus"]]
    ev = {e["event"] for e in events if e["salesorder_no"] == o["salesorder_no"]}
    if crm == "annulee":
        return "annulee"
    if crm == "livree" or "livraison" in ev:
        return "livree"
    if "expedition" in ev:
        return "expediee"
    if "affectation" in ev:
        return "en_preparation"
    return crm


def local_day(created):
    dt = datetime.fromisoformat(created.replace(" ", "T") + "+00:00")
    return dt.astimezone(ZoneInfo("Europe/Paris")).date().isoformat()


def lines(delta=None, start="0000", end="9999"):
    snap, orders, products, events, fx = load(delta=delta)
    returns = {(e["salesorder_no"], int(e["line_sequence"])): float(e["quantity"]) for e in events if e["event"] == "retour"}
    cur = snap["currency_map"]
    out = []
    for o in orders.values():
        day = local_day(o["createdtime"])
        if not (start <= day <= end):
            continue
        st = order_status(o, events)
        c = cur[o["currency_id"]]
        rate = 1.0 if c == "EUR" else fx.get((c, day))
        for li in o["LineItems"]:
            q = float(li["quantity"]); pu = float(li["listprice"])
            r = returns.get((o["salesorder_no"], int(li["sequence_no"])), 0.0)
            net = max(0.0, q * pu * (1 - float(li["discount_percent"]) / 100) - float(li["discount_amount"])) * (q - r) / q
            cost = products[li["productid"]]["purchase_cost"]
            out.append({"order": o["salesorder_no"], "account": o["account_id"], "status": st, "day": day,
                        "qty": q - r, "net_eur": net * rate if rate else None,
                        "cost_eur": float(cost) * (q - r) if cost else None})
    return out


def ca(**kw):
    return round(sum(l["net_eur"] for l in lines(**kw) if l["status"] in VALID and l["net_eur"] is not None), 6)


def marge(**kw):
    return round(sum(l["net_eur"] - l["cost_eur"] for l in lines(**kw)
                     if l["status"] in VALID and l["net_eur"] is not None and l["cost_eur"] is not None), 6)


def nb_commandes(**kw):
    return len({l["order"] for l in lines(**kw) if l["status"] in VALID})
