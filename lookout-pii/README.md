# lookout_pii.py

Récupère les infos PII des appareils depuis l'API MDM **Omnissa / Lookout EUC**
à partir d'une **liste** d'URL (ou d'ID d'appareil) et renvoie :
`guid, owner_name, email, phone_number, imsi, imei`.

## Le point clé : le `auth-token`

Le `auth-token` est un **JWT à courte durée de vie** — il expire régulièrement.
Le script le lit **séparément** du reste, donc quand il devient obsolète il suffit
de remplacer le jeton (pas le code). Ordre de priorité :

1. `--token "eyJ..."`
2. `--token-file token.txt`
3. variable d'environnement `LOOKOUT_AUTH_TOKEN`
4. fichier `token.txt` à côté du script

Quand l'API répond **401/403**, le script s'arrête immédiatement avec un message
clair : le jeton est expiré, il faut en récupérer un nouveau (onglet Réseau du
navigateur → une requête `/pii` → en-tête `auth-token`) et relancer.

## Utilisation

```bash
# jeton dans un fichier, liste d'URL, sortie CSV
python lookout_pii.py -i urls.txt --token-file token.txt -o resultats.csv

# jeton via variable d'environnement, sortie à l'écran
export LOOKOUT_AUTH_TOKEN="eyJ0eXAiOi..."
python lookout_pii.py -i urls.txt

# entrée = ID d'appareil seuls
python lookout_pii.py -i ids.txt --base-url https://omnissa-euc1.lookout.com \
    --connection a65ab8a2-5310-11ee-8e12-06323aa8b71b --token-file token.txt
```

## Fichier d'entrée (`urls.txt`)

Une entrée par ligne (`#` = commentaire) :

```
https://omnissa-euc1.lookout.com/mdm/v1/connections/<conn>/devices/517493/pii
https://omnissa-euc1.lookout.com/mdm/v1/connections/<conn>/devices/676307
517493
```

Le `/pii` est ajouté automatiquement s'il manque. Une ligne qui n'est qu'un ID
nécessite `--base-url` et `--connection`.

## Options

| Option | Rôle |
|--------|------|
| `-i, --input` | fichier liste (obligatoire) |
| `-o, --output` | fichier CSV (défaut : écran) |
| `--token / --token-file` | le auth-token |
| `--ent-guid` | en-tête `x-lookout-ent-guid` (optionnel) |
| `--base-url / --connection` | pour les entrées en ID seul |
| `--delay` | pause entre appels (défaut 0.2 s) |
| `--timeout` | timeout par requête (défaut 30 s) |

Aucune dépendance externe (bibliothèque standard uniquement).
