"""Moteur de métriques : compile une requête (métrique, axes, filtres, période) en SQL paramétré
à partir de models/semantic_model.yaml.

Règles anti-double-comptage :
  - une métrique est liée à UN fait de grain déclaré ;
  - seules les jointures many_to_one déclarées sur ce fait sont autorisées ; demander une
    dimension non joignable (ex. produit sur le fait commande) est refusé (fan-out) ;
  - le total est recalculé par une requête dédiée (jamais en additionnant des lignes), ce qui
    reste correct pour les métriques non additives et semi-additives.
Les noms (métrique, dimension, attribut) sont validés contre le modèle ; les valeurs sont
passées en paramètres liés. Aucun SQL fourni par l'appelant n'est exécuté.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from typing import Any

from okp.registry import MetricDef, Registry


class MetricError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


class MetricEngine:
    def __init__(self, conn: sqlite3.Connection, registry: Registry):
        self.conn = conn
        self.reg = registry
        self.sm = registry.semantic

    # ------------------------------------------------------------------ résolution des termes
    def resolve_metric(self, term: str) -> list[str]:
        t = term.strip().casefold()
        if term in self.sm.metrics:
            return [term]
        exact = [n for n, m in self.sm.metrics.items() if t in (n.casefold(), m.label.casefold())]
        if exact:
            return exact
        return [n for n, m in self.sm.metrics.items() if t in (s.casefold() for s in m.synonyms)]

    def describe(self, name: str) -> dict[str, Any]:
        m = self.sm.metrics[name]
        fact = self.sm.facts[m.fact]
        return {
            "name": name, "label": m.label, "description": m.description, "owner": m.owner,
            "fact": m.fact, "grain": fact.grain, "type": m.type,
            "formula": m.expression if m.type != "ratio" else f"({m.numerator}) / ({m.denominator})",
            "filters": m.filters, "unit": m.unit, "additivity": m.additivity, "additivity_note": m.additivity_note,
            "sensitivity": m.sensitivity, "rules": m.rules, "conventions": self.sm.conventions,
            "allowed_group_by": self.allowed_axes(m.fact), "synonyms": m.synonyms,
            "semantic_model_version": self.sm.version,
        }

    def allowed_axes(self, fact_name: str) -> list[str]:
        fact = self.sm.facts[fact_name]
        axes = []
        for d in fact.joins:
            axes += [f"{d}.{a}" for a in self.sm.dimensions[d].attributes]
        if "status" in fact.degenerate:
            axes.append("status")
        if "time" in fact.degenerate:
            axes += [f"time.{g}" for g in self.sm.dimensions["time"].grains]
        return axes

    # ------------------------------------------------------------------ compilation
    def _axis_sql(self, fact_name: str, axis: str, joins: set[str]) -> str:
        fact = self.sm.facts[fact_name]
        if axis == "status":
            if "status" not in fact.degenerate:
                raise MetricError("INVALID_DIMENSION", f"dimension status indisponible sur le fait {fact_name}")
            return "f." + self.sm.dimensions["status"].column
        dim, _, attr = axis.partition(".")
        if dim == "time":
            grains = self.sm.dimensions["time"].grains or {}
            if attr not in grains or "time" not in fact.degenerate:
                raise MetricError("INVALID_DIMENSION", f"grain temporel inconnu : {attr}", allowed=list(grains))
            return grains[attr].format(col="f." + fact.time_column)
        if dim not in self.sm.dimensions:
            raise MetricError("INVALID_DIMENSION", f"dimension inconnue : {dim}", allowed=list(self.sm.dimensions))
        if dim not in fact.joins:
            raise MetricError(
                "FAN_OUT", f"la dimension {dim} n'est pas joignable depuis le fait {fact_name} (grain : {fact.grain}) "
                f"sans risque de double comptage", allowed=self.allowed_axes(fact_name))
        if attr not in self.sm.dimensions[dim].attributes:
            raise MetricError("INVALID_DIMENSION", f"attribut inconnu : {axis}",
                              allowed=list(self.sm.dimensions[dim].attributes))
        joins.add(dim)
        return f"d_{dim}.{attr}"

    def _from_clause(self, fact_name: str, joins: set[str], pre_filters: list[str]) -> str:
        fact = self.sm.facts[fact_name]
        where = " AND ".join(f"({f})" for f in pre_filters) or "1=1"
        sql = f"(SELECT * FROM {fact.view} WHERE {where}) f"
        for dim in sorted(joins):
            d = self.sm.dimensions[dim]
            sql += f" LEFT JOIN {d.table} d_{dim} ON d_{dim}.{d.key} = f.{fact.joins[dim].column}"
        return sql

    def _where(self, fact_name: str, filters: dict[str, Any], period: dict | None, joins: set[str]):
        clauses, params = [], []
        for axis, values in (filters or {}).items():
            col = self._axis_sql(fact_name, axis, joins)
            vals = values if isinstance(values, (list, tuple)) else [values]
            if not vals:
                clauses.append("0=1")
                continue
            clauses.append(f"{col} IN ({', '.join('?' for _ in vals)})")
            params += [str(v) for v in vals]
        if period:
            tcol = "f." + self.sm.facts[fact_name].time_column
            for key, op in (("from", ">="), ("to", "<=")):
                if period.get(key):
                    try:
                        date.fromisoformat(period[key])
                    except (TypeError, ValueError):
                        raise MetricError("INVALID_PERIOD", f"date ISO attendue pour period.{key}") from None
                    clauses.append(f"{tcol} {op} ?")
                    params.append(period[key])
        return clauses, params

    def _aggregate(self, m: MetricDef) -> tuple[str, str | None]:
        if m.type == "sum" or m.type == "last_snapshot_sum":
            return f"SUM({m.expression})", None
        if m.type == "count_distinct":
            return f"COUNT(DISTINCT {m.expression})", None
        return m.numerator, m.denominator

    def query(self, name: str, group_by: list[str] | None = None, filters: dict | None = None,
              period: dict | None = None, security_filters: dict | None = None) -> dict[str, Any]:
        if name not in self.sm.metrics:
            raise MetricError("UNKNOWN_METRIC", f"métrique inconnue : {name}", available=list(self.sm.metrics))
        m = self.sm.metrics[name]
        group_by = list(dict.fromkeys(group_by or []))
        joins: set[str] = set()
        g_cols = [self._axis_sql(m.fact, a, joins) for a in group_by]
        try:
            s_clauses, s_params = self._where(m.fact, security_filters or {}, None, joins)
        except MetricError as exc:
            # Un filtre de sécurité inapplicable (dimension non joignable) = refus, jamais d'ignorance.
            raise MetricError("FORBIDDEN_SCOPE", "restriction de périmètre inapplicable à cette métrique",
                              reason=exc.message) from None
        u_clauses, u_params = self._where(m.fact, filters or {}, period, joins)
        clauses, params = s_clauses + u_clauses, s_params + u_params
        where = " AND ".join(clauses) or "1=1"
        from_sql = self._from_clause(m.fact, joins, m.filters)
        num, den = self._aggregate(m)

        if m.type == "last_snapshot_sum":
            fact = self.sm.facts[m.fact]
            time_axes = [a for a in group_by if a.startswith("time.")]
            bucket = self._axis_sql(m.fact, time_axes[0], joins) if time_axes else "'all'"
            base = (f"WITH base AS (SELECT f.*, {bucket} AS __bucket"
                    + "".join(f", {c} AS __g{i}" for i, c in enumerate(g_cols))
                    + f" FROM {from_sql} WHERE {where}), "
                    f"last AS (SELECT __bucket, MAX({fact.time_column}) AS __d FROM base GROUP BY __bucket) "
                    f"SELECT {{sel}} FROM base JOIN last ON last.__bucket = base.__bucket "
                    f"AND base.{fact.time_column} = last.__d {{grp}}")
            agg_num = num
        else:
            base = (f"WITH base AS (SELECT f.*" + "".join(f", {c} AS __g{i}" for i, c in enumerate(g_cols))
                    + f" FROM {from_sql} WHERE {where}) SELECT {{sel}} FROM base {{grp}}")
            agg_num = num

        def run(grouped: bool):
            sel = [f"__g{i}" for i in range(len(g_cols))] if grouped else []
            sel += [f"{agg_num} AS __num", "COUNT(*) AS __n"]
            if den:
                sel.append(f"{den} AS __den")
            grp = ""
            if grouped and g_cols:
                keys = ", ".join(f"__g{i}" for i in range(len(g_cols)))
                grp = f"GROUP BY {keys} ORDER BY {keys}"
            sql = base.format(sel=", ".join(sel), grp=grp)
            return sql, self.conn.execute(sql, params).fetchall()

        def value(r) -> float | int | None:
            if den:
                return None if not r["__den"] else _round(r["__num"] / r["__den"])
            return _round(r["__num"])

        total_sql, total_rows = run(False)
        total = total_rows[0]
        rows = []
        if group_by:
            _, grouped_rows = run(True)
            for r in grouped_rows:
                row = {a: r[f"__g{i}"] for i, a in enumerate(group_by)}
                row["value"] = value(r)
                if den:
                    row["numerator"], row["denominator"] = _round(r["__num"]), _round(r["__den"])
                row["row_count"] = r["__n"]
                rows.append(row)

        warnings = []
        for qc in m.quality_checks:
            qjoins = set(joins)
            qfrom = self._from_clause(m.fact, qjoins, [qc.count_filter])
            n = self.conn.execute(f"SELECT COUNT(*) FROM {qfrom} WHERE {where}", params).fetchone()[0]
            if n:
                warnings.append({"id": qc.id, "count": n, "message": f"{n} {qc.message}"})

        status = "ok" if total["__n"] else "no_data"
        run_info = self.conn.execute(
            "SELECT run_id, finished_at FROM ingestion_run WHERE status='succeeded' ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        return {
            "status": status,
            "metric": name,
            "label": m.label,
            "unit": m.unit,
            "value": value(total) if total["__n"] else None,
            "components": {"numerator": _round(total["__num"]), "denominator": _round(total["__den"])} if den else None,
            "row_count": total["__n"],
            "group_by": group_by,
            "rows": rows,
            "filters": filters or {},
            "security_filters": security_filters or {},
            "period": period or {},
            "warnings": warnings,
            "definition": self.describe(name),
            "provenance": {
                "kind": "computed",
                "computed_by": "okp.semantic.engine (déterministe)",
                "semantic_model_version": self.sm.version,
                "fact_view": self.sm.facts[m.fact].view,
                "source_systems": self.sm.facts[m.fact].sources,
                "data_as_of": run_info["finished_at"] if run_info else None,
                "ingestion_run": run_info["run_id"] if run_info else None,
                "sql": _compact(total_sql),
                "parameters": params,
            },
        }


def _round(v):
    if v is None:
        return None
    if isinstance(v, int):
        return v
    return round(float(v), 4)


def _compact(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()
