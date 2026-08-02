# Drop Monitor — état du projet

> Document de reprise. À lire en entier avant de toucher au code dans une
> nouvelle session. Il décrit ce qui existe, **pourquoi** c'est fait ainsi,
> les pièges rencontrés, et ce qui reste ouvert.

**État au 1er août 2026** — 357 tests passent, 155 fichiers Python,
47 fichiers TypeScript, 3 plugins, 12 tables SQLite.

---

## 1. Ce qu'est le projet

Une **plateforme de veille e-commerce orientée produit**. Point de départ :
être alerté sur Telegram dès qu'une précommande Pokémon ouvre chez
Micromania. Point d'arrivée actuel : un logiciel qui découvre lui-même les
fiches, comprend que plusieurs URL désignent le même produit, les surveille
toutes et explique chacune de ses décisions.

Le projet est **générique** : Pokémon n'est qu'un cas d'usage. Rien dans le
cœur ne connaît un site, un produit ou une marque.

⚠️ **Périmètre volontairement limité** : surveillance uniquement. Aucun
achat automatique, aucun contournement de protection (pas d'empreinte
falsifiée, pas de rotation de proxys, pas de résolution de CAPTCHA). Cette
règle a été posée dès le départ et n'a jamais été enfreinte — elle explique
plusieurs choix ci-dessous.

---

## 2. Lancer le projet

```bash
pip install -r requirements-dev.txt
playwright install chromium
python server.py
```

Dashboard : http://localhost:8000 · Swagger : `/api/docs`

```bash
pytest
```

⚠️ **Sous Windows**, `pytest` peut échouer sur le dossier temporaire par
défaut (`PermissionError` sur `pytest-of-…`). Utiliser :

```bash
pytest --basetemp=%TEMP%\dm-tests
```

Les 2 tests Playwright réels prennent ~45 s. Pour les sauter :
`pytest --ignore=tests/test_screenshot_playwright.py`

Docker / Railway : voir [DEPLOY.md](DEPLOY.md). L'image fait **1,9 Go**
(Chromium), se construit en 5–10 min la première fois.

---

## 3. Configuration réelle en place

Le `.env` est déjà rempli et **fonctionnel** (il est dans `.gitignore`) :

| Variable | État |
|---|---|
| `TELEGRAM_BOT_TOKEN` | bot **@pkmndrop_bot**, validé en réel |
| `TELEGRAM_CHAT_ID` | destinataire validé |
| `DASHBOARD_USERNAME` | `rayan` |
| `DASHBOARD_PASSWORD` | **voir `.env`** — ne jamais recopier ailleurs |
| `SECRET_KEY` | généré aléatoirement |

Trois fichiers de configuration :

- `config/products.yaml` — seed initial + réglages `defaults` (intervalles,
  retries, **confirmation des changements**, archivage des preuves)
- `config/discovery.yaml` — découverte automatique (**désactivée**),
  règles d'inclusion/exclusion, Product Intelligence, recherche inter-sites
  (**désactivée**)
- `.env` — secrets et chemins

**Contenu actuel de la base** : les 4 produits Pokémon 30 ans (désactivés,
URL vides — les fiches n'existent pas encore) et **`gta6` sur Micromania,
activé**, qui sert de produit de test réel.

---

## 4. Architecture

Un seul processus : FastAPI héberge l'API, le WebSocket, le dashboard
compilé **et** les moteurs, dans la même boucle asyncio. Pas de Redis, pas
de worker séparé. C'est ce qui rend le déploiement Railway trivial.

```
Découverte ──┐
             ├──▶ Event Bus ──┬──▶ SQLite (checks, timeline, alertes)
Surveillance ┘                ├──▶ Captures Playwright
                              ├──▶ WebSocket (dashboard temps réel)
                              └──▶ Telegram
Product Intelligence ─────────┘
```

### L'Event Bus est la colonne vertébrale

`src/core/events.py`. Le moteur ne connaît **aucun** consommateur : il
publie, les abonnés réagissent.

**L'ordre d'abonnement est significatif** et volontaire :

1. `EventRecorder` — persiste et pose `alert_id` dans le payload
2. `ScreenshotService` — enfile la capture (instantané) et pose
   `screenshot_pending`
3. `EventBroadcaster` — WebSocket (le dashboard n'attend ni Playwright ni
   Telegram)
4. `NotificationManager` — envoie, ou patiente si une capture est en cours

Changer cet ordre casse des garanties testées. Le bus exécute les abonnés
**séquentiellement** pour cette raison (un `gather` parallèle a été
abandonné).

### Les couches

| Dossier | Rôle |
|---|---|
| `src/core/` | moteur de surveillance, détecteur, event bus, preuves |
| `src/monitors/` | contrat plugin monitor, parser générique, registre |
| `src/discovery/` | découverte automatique : plugins, règles, fingerprint |
| `src/intelligence/` | produits canoniques, offres, matching, identité, recherche inter-sites |
| `src/db/` + `src/repositories/` | SQLAlchemy async + couche Repository |
| `src/services/` | recorder, captures, stats, diagnostic Telegram |
| `src/web/` | FastAPI, API v1, WebSocket, schémas Pydantic |
| `plugins/<site>/` | **tout ce qui est spécifique à un marchand** |
| `frontend/` | React + Vite + TS + Tailwind v4, PWA |

**Règle absolue** : aucun nom de site dans `src/`. Vérifié par test.

---

## 5. Les trois moteurs

### Monitor Engine (`src/core/engine.py`)

Une boucle asyncio par produit. **Réconcilie avec la base toutes les 5 s** :
ajouter, modifier, activer ou supprimer un produit prend effet à chaud,
sans redémarrage. Jitter ±10 %, retries à backoff exponentiel.

Baseline silencieuse au premier passage — jamais d'alerte sur la première
observation.

### Discovery Engine (`src/discovery/engine.py`)

Explore les sites, calcule une **empreinte stable** par fiche (EAN > SKU >
URL canonique, paramètres de tracking retirés), applique des règles
configurables, puis selon le mode : `auto` / `review` / `rules`.

Trois stratégies fournies et génériques : sitemap (robots.txt →
sitemap.xml), pages de listing, recherche.

### Product Intelligence Engine (`src/intelligence/`)

Le renversement clé : **une URL n'est qu'une offre**, le produit est une
entité sans URL.

Matching par échelle de confiance (100 EAN · 98 UPC · 96 ISBN · 95 MPN ·
93 ASIN · 92 SKU · 90 réf. · 88 modèle · 85 nom+marque · 80 nom+sortie ·
75 nom+collection · 70 nom seul). Au-dessus de `merge_threshold` (90) →
fusion automatique ; en dessous → **suggestion à valider**, jamais de
fusion silencieuse.

**Recherche multi-clés** : chaque information découverte devient une clé de
recherche chez tous les autres marchands. **Les échecs sont mémorisés**
(`search_attempts`) et retentés avec backoff 30 min → ×1,5 → 6 h, jusqu'à
ce que la fiche apparaisse.

---

## 6. Écrire un plugin

`plugins/amazon/` est **la référence**. Structure à copier :

```
plugins/<site>/
├── __init__.py     METADATA + exports
├── keywords.py     vocabulaire, aucune logique
├── parser.py       états typés, URL canonique
├── monitor.py      surveillance
├── identity.py     stratégie d'identité (auto-découverte)
└── discovery.py    exploration + search(identity, ctx, key)
```

Découverte **automatique** dans trois registres indépendants (monitors,
discovery, identity). Un plugin cassé est journalisé et ignoré — les autres
continuent. Tout est optionnel : un site peut n'avoir qu'un monitor.

- **Micromania** : hérite de `GenericHtmlMonitor` (mots-clés) — suffisant.
- **Amazon** : parser propre avec enum d'états. À suivre pour Fnac, Cultura,
  King Jouet, etc.

---

## 7. Fiabilité — les garde-fous

Principe : **mieux vaut manquer une alerte qu'en produire une fausse.**

1. **Confirmation** — aucun changement notifié sur une seule lecture. La
   page est relue (4 s après) ; si les lectures se contredisent, l'état
   précédent est conservé, la timeline note « État instable », **rien n'est
   envoyé**. Une lecture impossible n'est jamais une confirmation.
   Coût : une requête *uniquement* quand un changement apparaît.
2. **Aucun hash visible** — le hash est interne. Les alertes montrent des
   libellés lisibles.
3. **Preuve archivée** — le HTML ayant motivé une alerte importante est
   conservé dans `<DATA_DIR>/evidence/`, servi en **texte brut** (jamais
   exécuté dans le dashboard).
4. **Score de confiance** (Amazon) — 5 indices × 20 points. Sous 60,
   l'état devient `unknown`, rien n'est notifié.
5. **Nettoyage du bruit** — carrousels, recommandations, sponsorisés,
   navigation, pied de page retirés **avant** toute mesure, y compris du
   hash.

Réglages : bloc `defaults` de `config/products.yaml`.

---

## 8. Bugs réels trouvés et corrigés

Chacun a été découvert en testant, pas en relisant. Ils valent d'être
connus car plusieurs auraient cassé en production.

| Bug | Conséquence évitée |
|---|---|
| **Jetons base64 dans les boutons** — `_action_labels` lisait la `value` de *tous* les `<input>`, y compris les champs cachés d'Amazon | Hash instable + alertes illisibles (`JQAvtlJLg/XuwxxSTIqxA==`) |
| **`0000000000000` passe la clé de contrôle GTIN** | Aurait fusionné **à confiance 100** tous les produits dont un marchand met un EAN bidon |
| **Carrousel Micromania** — 145 boutons candidats, dont « Ajouter au panier » d'autres produits | Fausse alerte « retour en stock » + spam à chaque rotation |
| **`attempts += 1` sur `None`** — les défauts SQLAlchemy ne s'appliquent qu'au flush | Plantage à la première recherche inter-sites |
| **Candidat trouvé recréé en doublon** — un résultat obtenu *en cherchant ce produit* repartait en corrélation aveugle | Doublons dans le catalogue |
| **`create_app()` exécuté deux fois** — `uvicorn.run("server:app")` réimporte le module | Application construite en double |
| **Chemins Windows en base** (`2026-07-30\x.png`) | Captures introuvables après déploiement Linux |
| **Import circulaire** (×4 : db↔repositories, repositories↔intelligence, core↔services…) | Résolus par `TYPE_CHECKING` ou en remontant l'utilitaire partagé (`slugify` → `src/utils/text.py`) |
| **Logs httpx exposaient le token Telegram** | Fuite du token dans `logs/` |
| **Accents non normalisés** — `.lower()` seul | « PRECOMMANDER » jamais détecté |

Trois fois, **le code avait raison et mes tests avaient tort** (dédup de
clés identiques, `model_number` plus discriminant que `brand+model`,
garde-fou GTIN sur le corps du code). Ne pas « corriger » ces
comportements sans relire les commentaires.

---

## 9. Décisions à connaître (et discutables)

- **`INVITATION` → `PREORDER`** côté Amazon. Une demande d'invitation
  signifie que le drop est lancé. Conséquence : Telegram affiche
  « PRÉCOMMANDE OUVERTE » ; le libellé exact reste dans `status_text` et
  `details.etat_amazon`. Une ligne dans `STATE_TO_AVAILABILITY` pour changer.
- **Vendeur Amazon** : rotation entre revendeurs tiers = silence ;
  **Amazon → revendeur tiers = alerte** (état `THIRD_PARTY_ONLY`). Décision
  prise pour satisfaire « Amazon absent mais revendeur présent ».
- **Restriction au conteneur produit désactivée par défaut** — deviner le
  bon conteneur a échoué deux fois sur du HTML Micromania réel (bloc
  décoratif, puis `pdp-short-description` sans bouton d'achat). Le nettoyage
  du bruit suffit. Reste activable par plugin via `product_scope_selectors`.
- **Une seule instance** (`numReplicas: 1`). Deux répliques = deux moteurs
  sur la même base SQLite : requêtes dupliquées, alertes en double,
  corruption possible.
- **Pas de `PATCH /settings`** — la configuration vit dans les variables
  d'environnement (bonne pratique Railway). L'API expose la lecture masquée
  et le diagnostic Telegram.

---

## 10. Limites connues

**Le blocage IP est le vrai obstacle.** Micromania et Amazon refusent
souvent le trafic des hébergeurs. La même URL répond 200 chez vous et 403
depuis Railway. Le rendu navigateur résout les 403 dus à l'absence de
JavaScript, **pas** un blocage par plage d'IP — Chromium sort par la même
adresse. Remède : héberger la surveillance à la maison (Raspberry Pi, NAS,
PC). Voir la section dédiée dans `DEPLOY.md`.

**Fonctionnalités livrées mais désactivées**, en attente d'une décision :

| Fonction | Pourquoi désactivée |
|---|---|
| Découverte automatique | `config/discovery.yaml` → `enabled: false`. À activer en mode `review` d'abord. |
| Recherche inter-sites | Exige un `search_url_template` par marchand (non inventé). Amazon a un défaut fonctionnel. |
| Listings Micromania | `listing_urls: []` — aucune URL de catégorie inventée. |

**Non réalisé** : Discord/Email/SMS (interfaces prêtes), PostgreSQL (couche
Repository prête, changer `DATABASE_URL`), notifications push PWA (points
d'extension en place dans `registerSW.ts` et `sw.js`).

**Résidu connu** : sur la fiche Micromania réelle, une bannière promo
(« Précommandez maintenant EA Sports FC 27 ») est encore retenue comme
bouton. Sans effet sur ce produit, mais faux positif possible ailleurs.
Remède : un `product_scope_selectors` propre à Micromania, à définir en
observant le HTML.

---

## 11. Pièges de développement

- **`sqlite+aiosqlite:///:memory:`** donne une base **par connexion** du
  pool. Un script de démo a semblé perdre 7 écritures sur 10. Toujours
  utiliser une base fichier dans les tests (`tests/helpers.make_db`).
- **Défauts SQLAlchemy** appliqués au flush, pas à la construction. Toute
  colonne incrémentée après `Model(...)` doit être initialisée à la main.
- **`create_all` ne modifie jamais une table existante.** Ajouter une
  colonne = une entrée dans `MIGRATIONS` (`src/db/migrations.py`, v1→v3).
  Le mécanisme distingue base neuve et base en service.
- **Les logs `CHECK` (niveau 15)** ne s'affichent pas en console par défaut
  mais vont dans `logs/` et dans la page Logs. C'est là que vivent tous les
  diagnostics d'analyse.
- **PowerShell** : pas de heredoc, pas de `sleep` enchaîné. Écrire les
  scripts multi-lignes dans un fichier temporaire.
- **Ne jamais inventer d'URL produit.** Règle tenue depuis le début : les
  URL sont configurables et vides par défaut.

---

## 12. Par où continuer

Dans l'ordre de valeur, à mon avis :

1. **Activer la découverte en mode `review`** sur Micromania (sitemap) et
   observer une journée. C'est le plus gros gain immédiat.
2. **Renseigner `search_url_template`** pour Micromania → débloque la
   recherche inter-sites et la relance automatique, la brique la plus utile
   pour les drops échelonnés.
3. **Héberger à la maison** si les 403 persistent sur Railway.
4. **Un `product_scope_selectors` Micromania** pour éliminer le résidu de
   bannière promo.
5. **Un plugin Fnac** en copiant `plugins/amazon/`.

---

## 13. Documents

- `README.md` — installation, configuration, architecture, diagnostic
- `DEPLOY.md` — Docker, Railway, volume, blocage IP, dépannage
- `config/products.yaml` et `config/discovery.yaml` — abondamment commentés
- `/api/docs` — Swagger complet, en français
