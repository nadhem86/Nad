#!/usr/bin/env python3
"""Récupère les infos PII des appareils depuis l'API MDM Omnissa/Lookout EUC.

Le jeton `auth-token` est un JWT à durée de vie courte : il expire régulièrement.
Ce script le lit donc à part (option --token, fichier token.txt ou variable
d'environnement LOOKOUT_AUTH_TOKEN), pour qu'il suffise de le remplacer sans
toucher au reste. Il prend en entrée une LISTE d'URL (ou d'ID d'appareil) et
renvoie les valeurs : guid, owner_name, email, phone_number, imsi, imei.

Exemples
--------
    # jeton dans un fichier, liste d'URL dans un autre, sortie CSV
    python lookout_pii.py -i urls.txt --token-file token.txt -o resultats.csv

    # jeton via variable d'environnement
    export LOOKOUT_AUTH_TOKEN="eyJ0eXAiOi..."
    python lookout_pii.py -i urls.txt

Format du fichier d'entrée (une entrée par ligne, # = commentaire) :
    https://omnissa-euc1.lookout.com/mdm/v1/connections/<conn>/devices/517493/pii
    https://omnissa-euc1.lookout.com/mdm/v1/connections/<conn>/devices/517493   (le /pii est ajouté)
    517493   (ID seul : nécessite --base-url et --connection)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Colonnes renvoyées par l'endpoint /pii
FIELDS = ["guid", "owner_name", "email", "phone_number", "imsi", "imei"]


class TokenExpired(Exception):
    """Levée quand l'API répond 401/403 : le auth-token est obsolète."""


def load_token(args) -> str:
    """Charge le auth-token depuis --token, --token-file ou l'environnement."""
    if args.token:
        return args.token.strip()
    if args.token_file:
        with open(args.token_file, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    env = os.environ.get("LOOKOUT_AUTH_TOKEN")
    if env:
        return env.strip()
    # dernier recours : token.txt à côté du script
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")
    if os.path.exists(default):
        with open(default, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    sys.exit(
        "Aucun auth-token fourni. Utilise --token, --token-file, la variable "
        "LOOKOUT_AUTH_TOKEN ou un fichier token.txt."
    )


def normalize_target(line: str, base_url: str | None, connection: str | None) -> str:
    """Transforme une ligne d'entrée en URL complète vers /pii."""
    line = line.strip()
    if line.startswith("http://") or line.startswith("https://"):
        url = line.rstrip("/")
        if not url.endswith("/pii"):
            url += "/pii"
        return url
    # ligne = ID d'appareil seul -> il faut base_url + connection
    if not base_url or not connection:
        raise ValueError(
            f"'{line}' ressemble à un ID d'appareil mais --base-url et "
            "--connection ne sont pas fournis."
        )
    base = base_url.rstrip("/")
    return f"{base}/mdm/v1/connections/{connection}/devices/{line}/pii"


def read_targets(path: str, base_url, connection) -> list[str]:
    """Lit le fichier d'entrée et renvoie la liste d'URL normalisées."""
    targets = []
    with open(path, "r", encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            targets.append(normalize_target(line, base_url, connection))
    return targets


def fetch_pii(url: str, token: str, ent_guid: str | None, timeout: int) -> dict:
    """Appelle l'endpoint PII et renvoie le JSON décodé."""
    headers = {
        "auth-token": token,
        "accept": "application/json",
        "content-type": "application/json",
        "User-Agent": "lookout-pii-script/1.0",
    }
    if ent_guid:
        headers["x-lookout-ent-guid"] = ent_guid
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise TokenExpired(
                f"HTTP {exc.code} — le auth-token est probablement expiré. "
                "Récupère un nouveau jeton et relance."
            ) from exc
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code} sur {url} : {detail}") from exc


def main() -> int:
    p = argparse.ArgumentParser(
        description="Récupère les PII des appareils Omnissa/Lookout depuis une liste."
    )
    p.add_argument("-i", "--input", required=True,
                   help="Fichier liste (une URL ou un ID d'appareil par ligne).")
    p.add_argument("-o", "--output",
                   help="Fichier CSV de sortie (défaut : stdout).")
    p.add_argument("--token", help="auth-token en clair (JWT).")
    p.add_argument("--token-file", help="Fichier contenant le auth-token.")
    p.add_argument("--ent-guid",
                   help="Valeur de l'en-tête x-lookout-ent-guid (optionnel).")
    p.add_argument("--base-url",
                   help="Base (ex. https://omnissa-euc1.lookout.com) si l'entrée "
                        "ne contient que des ID.")
    p.add_argument("--connection",
                   help="GUID de connexion MDM si l'entrée ne contient que des ID.")
    p.add_argument("--delay", type=float, default=0.2,
                   help="Pause entre appels en secondes (défaut 0.2).")
    p.add_argument("--timeout", type=int, default=30,
                   help="Timeout par requête en secondes (défaut 30).")
    args = p.parse_args()

    token = load_token(args)
    targets = read_targets(args.input, args.base_url, args.connection)
    if not targets:
        sys.exit("Le fichier d'entrée ne contient aucune cible.")

    out_fh = open(args.output, "w", newline="", encoding="utf-8") if args.output else sys.stdout
    writer = csv.DictWriter(out_fh, fieldnames=["source_url", *FIELDS, "error"])
    writer.writeheader()

    ok = errors = 0
    for idx, url in enumerate(targets, 1):
        row = {"source_url": url}
        try:
            data = fetch_pii(url, token, args.ent_guid, args.timeout)
            for f in FIELDS:
                row[f] = data.get(f, "")
            ok += 1
        except TokenExpired as exc:
            # Inutile de continuer : toutes les requêtes suivantes échoueront.
            print(f"\n[STOP] {exc}", file=sys.stderr)
            row["error"] = "auth-token expiré"
            writer.writerow(row)
            break
        except Exception as exc:  # noqa: BLE001
            row["error"] = str(exc)
            errors += 1
            print(f"[{idx}/{len(targets)}] ERREUR {url} : {exc}", file=sys.stderr)
        else:
            print(f"[{idx}/{len(targets)}] OK {url}", file=sys.stderr)
        writer.writerow(row)
        if args.delay:
            time.sleep(args.delay)

    if out_fh is not sys.stdout:
        out_fh.close()
    print(f"\nTerminé : {ok} OK, {errors} erreurs sur {len(targets)} cibles.",
          file=sys.stderr)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
