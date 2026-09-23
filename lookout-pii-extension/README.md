# Extension Edge — Lookout PII (export par lot)

Exporte en CSV les infos PII (`guid, owner_name, email, phone_number, imsi, imei`)
des appareils Omnissa / Lookout EUC, à partir d'une **liste de liens d'issues**
de la console.

## Ce que ça règle

- **Le `auth-token` qui expire :** l'extension le **capte automatiquement** dans
  le trafic de la console pendant que tu navigues. Tu n'as plus jamais à copier
  ni coller le JWT. Quand il expire, il suffit de recharger la console : le
  nouveau jeton est repris tout seul.
- **Le passage issue → appareil :** l'extension **apprend** le schéma toute seule
  (voir plus bas), donc pas besoin de connaître l'API à l'avance.

## Installation dans Edge

1. Ouvre `edge://extensions/`.
2. Active le **Mode développeur** (interrupteur en bas à gauche).
3. Clique **Charger une extension décompressée** (« Load unpacked »).
4. Sélectionne le dossier `lookout-pii-extension/`.
5. L'icône bleue apparaît dans la barre d'outils (épingle-la si besoin).

## Utilisation

1. Connecte-toi à la console Lookout et **ouvre une issue** (n'importe laquelle).
   → l'extension capte le `auth-token`, l'`ent-guid` et la connexion.
2. Clique l'icône de l'extension puis **Apprendre** (une seule fois par tenant).
   Le popup doit afficher « schéma issue : appris ✔ ».
3. Colle tes **liens d'issues** (un par ligne) dans la zone de texte —
   exactement ceux de ta colonne Excel :
   ```
   https://omnissa-euc1.lookout.com/bc20e166-…/issues/8c045d3e-…
   https://omnissa-euc1.lookout.com/bc20e166-…/issues/0f72aa81-…
   ```
4. Clique **Exporter en CSV**. Le fichier `lookout-pii-AAAA-MM-JJ-….csv` est
   téléchargé, avec une ligne par issue.

Si une exécution s'arrête sur « token expiré », recharge simplement la console
(pour re-capter un jeton frais) et relance l'export.

## Le CSV de sortie

| colonne | contenu |
|---------|---------|
| `issue_guid` | GUID de l'issue |
| `device_id` | ID de l'appareil résolu |
| `connection` | GUID de connexion MDM |
| `guid, owner_name, email, phone_number, imsi, imei` | valeurs PII |
| `error` | vide si OK, sinon la raison |

## Si « Apprendre » échoue

Rare, mais possible si la console structure ses réponses autrement. Ouvre les
**Options** de l'extension (clic droit sur l'icône → Options) et renseigne
manuellement, depuis l'onglet Réseau (F12) d'une issue ouverte :

- l'URL de l'endpoint issue avec `{ISSUE_GUID}` à la place du GUID ;
- le chemin du `device_id` dans la réponse (ex. `data.device.id`) ;
- le chemin de la connexion, ou une connexion fixe.

## Confidentialité

Tout est **local**. L'extension ne parle qu'à `https://*.lookout.com`, ne stocke
le jeton que dans `chrome.storage.local` de ton navigateur, et n'envoie rien à
un tiers. Le bouton **Réinitialiser** efface tout ce qui est mémorisé.
