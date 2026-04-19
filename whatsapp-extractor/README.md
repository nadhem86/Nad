# WhatsApp Web → vTiger CRM — Extension Chrome

Extrait automatiquement les numéros de téléphone et les informations des contacts depuis **WhatsApp Web** et les synchronise directement dans **vTiger CRM** via son API REST.

---

## Fonctionnalités

| Fonctionnalité | Détail |
|---|---|
| Extraction multi-méthodes | Store JS interne WhatsApp, modules webpack, scan DOM |
| Données extraites | Numéro, nom, prénom court, Business, muet, non-lus, dernière activité |
| Sync vTiger | Création, mise à jour ou ignoré selon les doublons |
| Module cible | Contacts ou Prospects (Leads) |
| Gestion doublons | Skip / Update / Always create |
| Export local | CSV et JSON |
| Recherche | Filtrage en temps réel par nom ou numéro |
| Persistance | Cache local (chrome.storage) + dernier rapport de sync |

---

## Architecture des fichiers

```
whatsapp-extractor/
├── manifest.json        # Déclaration Manifest V3
├── content.js           # Injecté dans WhatsApp Web — extraction
├── vtiger.js            # Client API vTiger (auth MD5, CRUD)
├── background.js        # Service Worker — proxy API sans CORS
├── popup.html           # Interface principale
├── popup.css            # Styles popup
├── popup.js             # Logique popup
├── options.html         # Page paramètres vTiger
├── options.css          # Styles paramètres
├── options.js           # Logique paramètres
└── icons/
    ├── icon16.png
    ├── icon48.png
    └── icon128.png
```

---

## Installation

### Prérequis

- Google Chrome (version 88+) ou Chromium
- Un compte **WhatsApp Web** connecté sur `web.whatsapp.com`
- Une instance **vTiger CRM** accessible (auto-hébergée ou SaaS)

### Étape 1 — Télécharger l'extension

```bash
git clone https://github.com/nadhem86/Nad.git
cd Nad
```

Ou téléchargez le dossier `whatsapp-extractor/` directement.

### Étape 2 — Charger dans Chrome

1. Ouvrez Chrome et allez sur `chrome://extensions/`
2. Activez le **Mode développeur** (interrupteur en haut à droite)
3. Cliquez sur **"Charger l'extension non empaquetée"**
4. Sélectionnez le dossier `whatsapp-extractor/`
5. L'icône WhatsApp apparaît dans la barre d'outils Chrome

> Pour l'épingler : cliquez sur l'icône puzzle → épingle à côté de "WA → vTiger"

---

## Configuration vTiger

### Obtenir votre clé d'accès API

1. Connectez-vous à votre vTiger CRM
2. Cliquez sur votre **nom d'utilisateur** en haut à droite
3. Allez dans **Mon profil**
4. Onglet **Avancé** → copiez la valeur **Clé d'accès**

### Configurer l'extension

1. Cliquez sur l'icône engrenage (⚙) dans le popup de l'extension
2. Remplissez les champs :
   - **URL vTiger** : `https://votre-instance.vtiger.com` *(sans `/webservice.php`)*
   - **Nom d'utilisateur** : votre login vTiger
   - **Clé d'accès** : la clé copiée ci-dessus
3. Cliquez **"Tester la connexion"** pour valider
4. Cliquez **"Enregistrer"**

### Options de synchronisation

| Option | Valeurs disponibles |
|---|---|
| Gestion des doublons | Ignorer / Mettre à jour / Toujours créer |
| Module cible | Contacts / Prospects (Leads) |
| Tag Business | Oui / Non |
| Source = WhatsApp | Oui / Non |
| Contacts sans nom | Inclure / Exclure |

---

## Utilisation

### 1. Extraire les contacts WhatsApp

1. Ouvrez `https://web.whatsapp.com` dans un onglet Chrome
2. Assurez-vous d'être **connecté** (QR code scanné)
3. Naviguez dans quelques conversations pour charger les contacts en mémoire
4. Cliquez sur l'icône de l'extension dans la barre d'outils
5. Cliquez **"Extraire"**

> **Astuce** : faites défiler votre liste de conversations pour charger davantage de contacts dans le Store JS de WhatsApp.

### 2. Rechercher et filtrer

Utilisez la barre de recherche pour filtrer par **nom** ou **numéro de téléphone**.

### 3. Synchroniser vers vTiger

1. Après extraction, cliquez **"Sync vTiger"**
2. Une barre de progression s'affiche
3. Le résultat indique :
   - `+N créés` — nouveaux contacts créés dans vTiger
   - `N mis à jour` — contacts existants mis à jour
   - `N ignorés` — doublons ignorés (selon option choisie)
   - `N erreurs` — contacts non traités (voir console)

### 4. Exporter localement

- **CSV** : ouvre un téléchargement `contacts-whatsapp.csv`
- **JSON** : ouvre un téléchargement `contacts-whatsapp.json`

---

## Mapping des champs WhatsApp → vTiger

| Champ WhatsApp | Champ vTiger |
|---|---|
| Prénom (1er mot du nom) | `firstname` |
| Nom (reste) | `lastname` |
| Numéro WhatsApp | `phone` + `mobile` |
| Type Business | Note dans `description` |
| Dernière activité | Note dans `description` |
| Source | `leadsource` = "WhatsApp" |

---

## Méthodes d'extraction (par priorité)

### 1. `window.Store` (Store JS interne)

WhatsApp Web expose un objet JavaScript global `window.Store` contenant l'intégralité des contacts et conversations chargées. C'est la méthode la plus fiable et la plus complète.

- `Store.Contact.models` → liste complète des contacts
- `Store.Chat.models` → conversations actives avec métadonnées

### 2. Modules webpack (`require()`)

Fallback via le système de modules interne de WhatsApp Web (`WAWebContactStore`, `WAWebChatStore`).

### 3. Scan DOM

Analyse des attributs `data-testid` dans la liste de conversations et le panneau d'information du contact. Extrait les numéros via expression régulière.

---

## Indicateurs visuels

| Élément | Signification |
|---|---|
| Badge vert "Extrait" | Extraction réussie |
| Badge orange "Sync…" | Synchronisation en cours |
| Badge rouge "Erreur" | Problème rencontré |
| Point vert (bas droite) | vTiger configuré et accessible |
| Point gris (bas droite) | vTiger non configuré |

---

## Dépannage

### "Aucun onglet WhatsApp Web trouvé"
→ Ouvrez `https://web.whatsapp.com` dans un onglet Chrome **avant** de cliquer sur Extraire.

### "Aucun contact trouvé"
→ WhatsApp Web charge les contacts à la demande. Faites défiler votre liste de conversations, ouvrez quelques chats, puis réessayez.

### Erreur de connexion vTiger
→ Vérifiez :
- L'URL ne doit pas contenir `/webservice.php` (l'extension l'ajoute automatiquement)
- Le nom d'utilisateur est bien en minuscules
- La clé d'accès est copiée sans espace

### Le bouton "Sync vTiger" est grisé
→ Configurez vos identifiants vTiger via l'icône ⚙ (paramètres).

---

## Sécurité et confidentialité

- Aucune donnée n'est envoyée vers des serveurs tiers
- Les identifiants vTiger sont stockés localement dans `chrome.storage.local` (chiffré par Chrome, accessible uniquement par cette extension)
- L'extension ne lit que les onglets WhatsApp Web (`https://web.whatsapp.com/*`)
- Les appels vTiger sont effectués depuis le Service Worker (évite les problèmes CORS)

---

## Permissions requises

| Permission | Utilisation |
|---|---|
| `activeTab` | Accès à l'onglet WhatsApp Web actif |
| `scripting` | Injection du script d'extraction |
| `storage` | Cache local des contacts et configuration |
| `downloads` | Export CSV / JSON |
| `host_permissions: web.whatsapp.com` | Lecture du DOM et du Store JS WhatsApp |

---

## Licence

Usage personnel et professionnel libre. Respectez les [Conditions d'utilisation de WhatsApp](https://www.whatsapp.com/legal/terms-of-service) et de vTiger lors de l'utilisation de cette extension.
