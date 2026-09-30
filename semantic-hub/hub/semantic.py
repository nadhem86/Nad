"""Moteur de métriques : compile les définitions de semantic_model/model.yaml en SQL paramétré.

Garanties :
- seuls les chemins de jointure déclarés sont utilisés ;
- une dimension N-N (pont) ou non déclarée pour la table de la métrique est REFUSÉE (pas de double comptage) ;
- les lignes exclues pour valeur manquante sont comptées et renvoyées dans `warnings` ;
- « aucune donnée » (NULL) est distinct de zéro ;
- le périmètre du principal (régions) est appliqué dans le SQL, côté serveur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class MetricError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code  # UNKNOWN_METRIC | INVALID_DIMENSION | FAN_OUT | INVALID_FILTER


@dataclass
class MetricQuery:
    metrics: list[str]
    group_by: list[str] = field(default_factory=list)       # ex. ["client.nom", "temps.trimestre"]
    filters: dict[str, Any] = field(default_factory=dict)   # ex. {"client.region": "Nord"}
    period_start: str | None = None                          # date locale incluse (YYYY-MM-DD)
    period_end: str | None = None


class SemanticModel:
    def __init__(self, path: Path):
        self.raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.version = self.raw["version"]
        self.dimensions = self.raw["dimensions"]
        self.tables = self.raw["tables"]
        self.metrics = self.raw["metrics"]
        self.common_rules = self.raw["common_rules"]

    def metric_card(self, mid: str) -> dict:
        m = self.metrics[mid]
        return {
            "id": mid, "label": m["label"], "definition": m["definition"], "owner": m["owner"],
            "unit": m["unit"], "additivity": m["additivity"], "additivity_note": m.get("additivity_note"),
            "grain": self.tables[m["table"]]["grain"], "table": m["table"],
            "allowed_dimensions": self.allowed_dimensions(mid),
            "formula_sql": m["expression"],
            "filters": [self.common_rules.get(f, f) for f in m.get("filters", [])],
            "rules": {r: self.common_rules[r] for r in m.get("rules", [])},
            "sensitivity": m.get("sensitivity", "interne"),
            "model_version": self.version,
        }

    def allowed_dimensions(self, mid: str) -> list[str]:
        m = self.metrics[mid]
        dims = self.tables[m["table"]]["dimensions"]
        forbidden = set(m.get("forbidden_dimensions", []))
        return [d for d in dims if d not in forbidden and not self.dimensions[d].get("many_to_many")]


class MetricEngine:
    def __init__(self, conn, model: SemanticModel):
        self.conn = conn
        self.model = model

    # ------------------------------------------------------------------ compilation
    def _dim_expr(self, table: str, ref: str) -> tuple[str, str | None]:
        """'client.region' -> (expression SQL, dimension à joindre ou None)."""
        if "." not in ref:
            raise MetricError("INVALID_DIMENSION", f"référence de dimension invalide : {ref!r} (attendu dim.attribut)")
        dim, attr = ref.split(".", 1)
        d = self.model.dimensions.get(dim)
        if d is None or attr not in d["attributes"]:
            raise MetricError("INVALID_DIMENSION", f"dimension inconnue : {ref!r}")
        col = d["attributes"][attr]
        if "table" in d:
            return f"{d['table']}.{col}", dim
        # dimension portée par la table de faits (statut, temps)
        return re.sub(r"\b(date_locale|statut)\b", lambda m: f"{table}.{m.group(1)}", col), None

    def _check_dimension(self, mid: str, dim: str | None, ref: str) -> None:
        if dim is None:
            table = self.model.metrics[mid]["table"]
            base = ref.split(".")[0]
            if base not in self.model.tables[table]["dimensions"]:
                raise MetricError("INVALID_DIMENSION", f"{ref} n'est pas disponible pour {mid} (table {table})")
            return
        if self.model.dimensions[dim].get("many_to_many"):
            raise MetricError("FAN_OUT", (
                f"La dimension '{dim}' passe par une relation N-N (un produit peut avoir plusieurs fournisseurs) : "
                f"agréger '{mid}' par {dim} compterait plusieurs fois la même ligne. Utilisez une analyse d'impact "
                f"(relations) ou une règle d'allocation explicite, non définie en v{self.model.version}."))
        if dim not in self.model.allowed_dimensions(mid):
            raise MetricError("INVALID_DIMENSION", f"la dimension '{dim}' n'est pas autorisée pour {mid} : "
                                                   f"{self.model.metrics[mid].get('additivity_note', '')}")

    def compile(self, mid: str, q: MetricQuery, region_scope: list[str] | None = None,
                extra_where: list[str] | None = None,
                required: dict[str, str] | None = None) -> tuple[str, list[Any], list[str]]:
        """required : col -> 'notnull' (défaut) | 'null' | 'skip' ; utilisé pour compter les exclusions."""
        if mid not in self.model.metrics:
            raise MetricError("UNKNOWN_METRIC", f"métrique inconnue : {mid!r}. Catalogue : {sorted(self.model.metrics)}")
        m = self.model.metrics[mid]
        table = m["table"]
        tdef = self.model.tables[table]
        joins: dict[str, str] = {}
        selects, groups, where, args = [], [], [], []

        for i, ref in enumerate(q.group_by):
            expr, dim = self._dim_expr(table, ref)
            self._check_dimension(mid, dim, ref)
            if dim:
                joins[dim] = tdef["joins"][dim]
            selects.append(f"{expr} AS g{i}")
            groups.append(f"g{i}")
        for ref, val in q.filters.items():
            expr, dim = self._dim_expr(table, ref)
            self._check_dimension(mid, dim, ref)
            if dim:
                joins[dim] = tdef["joins"][dim]
            vals = val if isinstance(val, list) else [val]
            where.append(f"{expr} IN ({','.join('?' * len(vals))})")
            args += vals

        for f in m.get("filters", []):
            cond = self.model.common_rules.get(f, f)
            where.append(re.sub(r"(?<![.\w])statut\b", f"{table}.statut", cond))
        for col in m.get("required_columns", []):
            mode = (required or {}).get(col, "notnull")
            if mode != "skip":
                where.append(f"{table}.{col} IS {'NOT ' if mode == 'notnull' else ''}NULL")
        where += extra_where or []

        semi = m.get("semi_additive")
        if semi:
            tc = f"{table}.{semi['time_column']}"
            sub = f"SELECT MAX({semi['time_column']}) FROM {table}"
            sub_where, sub_args = [], []
            if q.period_start:
                sub_where.append(f"{semi['time_column']} >= ?"); sub_args.append(q.period_start)
            if q.period_end:
                sub_where.append(f"{semi['time_column']} <= ?"); sub_args.append(q.period_end)
            if sub_where:
                sub += " WHERE " + " AND ".join(sub_where)
            where.append(f"{tc} = ({sub})")
            args += sub_args
        else:
            if q.period_start or q.period_end:
                if "temps" not in tdef["dimensions"]:
                    raise MetricError("INVALID_FILTER", f"{mid} n'a pas de dimension temps")
                if q.period_start:
                    where.append(f"{table}.date_locale >= ?"); args.append(q.period_start)
                if q.period_end:
                    where.append(f"{table}.date_locale <= ?"); args.append(q.period_end)

        if region_scope is not None:
            if "client" not in tdef["dimensions"]:
                raise MetricError("INVALID_FILTER", f"{mid} ne peut pas être restreint par région client")
            joins["client"] = tdef["joins"]["client"]
            where.append(f"dim_client.region IN ({','.join('?' * len(region_scope)) or 'NULL'})")
            args += region_scope

        sql = f"SELECT {', '.join(selects + [m['expression'] + ' AS value', 'COUNT(*) AS n_rows'])} FROM {table}"
        for dim, cond in joins.items():
            sql += f" JOIN {self.model.dimensions[dim]['table']} ON {cond}"
        if where:
            sql += " WHERE " + " AND ".join(f"({w})" for w in where)
        if groups:
            sql += f" GROUP BY {', '.join(groups)} ORDER BY {', '.join(groups)}"
        return sql, args, groups

    # ------------------------------------------------------------------ exécution
    def run(self, q: MetricQuery, region_scope: list[str] | None = None) -> dict:
        results = {}
        for mid in q.metrics:
            sql, args, groups = self.compile(mid, q, region_scope)
            rows = self.conn.execute(sql, args).fetchall()
            data = [{**{ref: r[f"g{i}"] for i, ref in enumerate(q.group_by)},
                     "value": None if r["value"] is None else round(r["value"], 6), "n_rows": r["n_rows"]}
                    for r in rows if not (not groups and r["n_rows"] == 0)]
            results[mid] = {
                "rows": data,
                "status": "ok" if any(d["value"] is not None for d in data) else "no_data",
                "warnings": self._exclusions(mid, q, region_scope),
                "definition": self.model.metric_card(mid),
                "sql": sql,
                "params": args,
            }
        return results

    def _exclusions(self, mid: str, q: MetricQuery, region_scope: list[str] | None) -> list[dict]:
        """Lignes écartées faute de valeur (taux de change, coût...) : jamais silencieux.
        Chaque ligne est comptée une seule fois, pour la PREMIÈRE colonne requise manquante."""
        m = self.model.metrics[mid]
        reason_cols = {"montant_net_eur": "montant_manquant_raison", "cout_eur": "cout_manquant_raison"}
        required = m.get("required_columns", [])
        base = MetricQuery(metrics=[mid], filters=q.filters, period_start=q.period_start, period_end=q.period_end)
        out = []
        for i, col in enumerate(required):
            rc = reason_cols.get(col)
            if not rc:
                continue
            modes = {c: ("notnull" if j < i else "null" if j == i else "skip") for j, c in enumerate(required)}
            out += self._count_by_reason(mid, base, region_scope, rc, required=modes, key="excluded_rows", column=col)
        if "cout_eur" in required:
            out += self._count_by_reason(
                mid, base, region_scope, "cout_manquant_raison", key="approximated_rows", column="cout_eur",
                extra_where=["fct_ligne.cout_manquant_raison = 'cout_courant_utilise_faute_d_historique'"])
        return out

    def _count_by_reason(self, mid, q, region_scope, reason_col, key, column, required=None, extra_where=None):
        table = self.model.metrics[mid]["table"]
        sql, args, _ = self.compile(mid, q, region_scope, extra_where=extra_where, required=required)
        sql = re.sub(r"^SELECT .*? FROM", f"SELECT {table}.{reason_col} AS reason, COUNT(*) AS n FROM", sql, count=1)
        sql += f" GROUP BY {table}.{reason_col}"
        return [{key: r["n"], "column": column, "reason": r["reason"]} for r in self.conn.execute(sql, args)]
