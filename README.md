# Drop Monitor 🎴

Surveillance de disponibilité de produits (précommandes, retours en stock) avec
alertes **Telegram** immédiates. Conçu pour les produits Pokémon TCG 30e
anniversaire chez Micromania, extensible à n'importe quel site marchand.

> ⚠️ Ce projet est un **système de surveillance uniquement**. Il n'achète rien
> automatiquement et ne contourne aucune protection de site.

## Architecture

```
notifdrop/
├── server.py                   # Point d'entrée web (API + dashboard + moteur)
├── main.py                     # Point d'entrée CLI (surveillance seule)
├── Dockerfile                  # Image de production (multi-stage)
├── docker-compose.yml          # Déploiement local / serveur
├── railway.json                # Healthcheck, restart, 1 instance
├── config/
│   └── products.yaml           # Seed initial des produits
├── plugins/                    # UN DOSSIER PAR SITE, découverte automatique
│   └── micromania/             # metadata, keywords, selectors, parser, monitor
├── src/
│   ├── config/                 # .env + YAML (validation)
│   ├── core/
│   │   ├── engine.py           # Une boucle asyncio par produit, rechargée à chaud
│   │   ├── detector.py         # Diff entre deux états → événements
│   │   └── events.py           # Event bus (moteur → DB / captures / WS / Telegram)
│   ├── models/                 # Dataclasses typées (produit, snapshot, événement)
│   ├── monitors/               # base, generic, plugin, loader, registry
│   ├── discovery/              # Découverte : plugins, règles, fingerprint
│   ├── intelligence/           # Product Intelligence : entités, matching,
│   │                           # identifiants, recherche inter-sites
│   ├── db/                     # SQLAlchemy : schéma, migrations, seed
│   ├── repositories/           # Couche Repository (SQLite → PostgreSQL)
│   ├── notifications/          # base, telegram (photo + texte), manager
│   ├── services/
│   │   ├── recorder.py         # Bus → base (checks, timeline, alertes)
│   │   ├── stats.py            # Agrégats du dashboard
│   │   └── screenshots/        # Playwright : pool, capture, cookies, policy
│   ├── web/
│   │   ├── app.py              # Usine FastAPI + lifespan
│   │   ├── api/                # Routers /api/v1
│   │   ├── schemas/            # Contrat public (Pydantic)
│   │   └── ws.py               # WebSocket temps réel
│   └── utils/                  # Logs (console, fichiers, buffer API), état
├── frontend/                   # React + Vite + Tailwind + PWA
│   ├── public/                 # manifest, service worker, icônes
│   └── src/                    # api, components/ui, components/domain, pages, ws
├── data/                       # SQLite + captures (volume en production)
├── logs/                       # Fichiers de logs
└── tests/                      # 111 tests (pytest)
```

## Installation

Prérequis : **Python 3.10+** (ou simplement Docker, voir
[DEPLOY.md](DEPLOY.md)).

```bash
cd notifdrop
python -m venv .venv
.venv\Scripts\activate        # Windows  (Linux/macOS : source .venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env        # Windows  (Linux/macOS : cp .env.example .env)
```

`requirements.txt` ne contient que les dépendances de production (image
Docker) ; `requirements-dev.txt` y ajoute l'outillage de test.

## Création du Bot Telegram

1. Dans Telegram, ouvrir une conversation avec **@BotFather**.
2. Envoyer `/newbot`, choisir un nom puis un identifiant (ex. `mon_drop_monitor_bot`).
3. BotFather renvoie un **token** de la forme `123456789:AAH4...xyz` → le copier
   dans `.env` :

   ```
   TELEGRAM_BOT_TOKEN=123456789:AAH4...xyz
   ```

## Récupération du Chat ID

1. Envoyer **n'importe quel message** à votre bot (indispensable : un bot ne
   peut pas écrire en premier).
2. Ouvrir dans un navigateur :

   ```
   https://api.telegram.org/bot<VOTRE_TOKEN>/getUpdates
   ```

3. Chercher `"chat":{"id":123456789,...}` dans la réponse → copier ce nombre
   dans `.env` :

   ```
   TELEGRAM_CHAT_ID=123456789
   ```

   *(Alternative : envoyer un message à **@userinfobot**, qui affiche votre ID.)*

4. Vérifier que tout fonctionne :

```bash
python main.py --test-telegram
```

Vous devez recevoir une fausse alerte « Message de test » sur Telegram.

## Base de données (SQLite)

Depuis la v2, **la base SQLite est la source de vérité des produits**
(`data/drop_monitor.db`, créée automatiquement) :

- au **premier démarrage**, les produits de `config/products.yaml` sont
  importés en base (seed) ; le YAML n'est plus relu ensuite ;
- chaque produit reçoit un **uuid immuable**, une **priority**
  (low/normal/high/critical) et des **tags** libres ;
- le moteur relit la base toutes les 5 secondes : toute modification
  (ajout, URL, intervalle, activation, suppression) prend effet **à chaud,
  sans redémarrage** — c'est ce qu'utilisera le dashboard ;
- l'historique est persisté : table `checks` (vérifications + temps de
  réponse), `timeline_events` (la vie complète de chaque produit),
  `alerts` (alertes envoyées), `snapshots` (dernier état connu) ;
- passer à PostgreSQL plus tard = définir `DATABASE_URL`, rien d'autre.

Pour repartir de zéro (et re-déclencher le seed YAML) : supprimer
`data/drop_monitor.db`.

## Configuration des produits (seed initial)

Tout se passe dans [config/products.yaml](config/products.yaml) :

```yaml
products:
  - name: "Pokémon 30 Ans ETB"
    site: micromania
    url: ""                 # ← à remplir quand la page existera
    check_interval: 60      # secondes
    enabled: false          # ← passer à true quand l'URL est remplie
```

### Ajouter une URL (le jour du drop)

1. Coller l'URL de la fiche produit dans le champ `url`.
2. Passer `enabled: true`.
3. Relancer le programme. C'est tout — aucun code à modifier.

> Au **premier passage** sur un produit, le programme enregistre simplement
> l'état actuel (baseline) **sans envoyer d'alerte**. Les alertes ne partent
> que lorsqu'un **changement** est détecté ensuite : ouverture de précommande,
> retour en stock, apparition d'un prix, changement de bouton ou de statut.

### Ajouter un nouveau produit

Ajouter un bloc dans `products:` avec un `name` unique, le `site`, l'`url`,
l'intervalle et `enabled`.

## Product Intelligence Engine — raisonner en produits, plus en URL

Une URL n'est qu'une **offre** d'un marchand pour un produit. Le catalogue
raisonne donc en deux entités :

| Entité | Contenu | Ce qu'elle n'a pas |
|---|---|---|
| **Product** | nom canonique, marque, collection, édition, catégorie, date de sortie, image, EAN/UPC/ISBN/MPN/SKU, tags, priorité | **aucune URL** |
| **Offer** | site, URL, prix, devise, disponibilité, statut, historique | — |

Un produit possède plusieurs offres. **Une offre n'est jamais supprimée** :
elle change d'état (`active`, `inactive`, `not_found`, `removed`,
`archived`) pour que l'historique reste entier.

### Corrélation automatique

Quand une fiche est découverte, le moteur cherche si elle représente un
produit déjà connu. Les méthodes sont ordonnées par confiance :

| Score | Méthode |
|---|---|
| 100 | EAN identique |
| 98 | UPC identique |
| 96 | ISBN identique |
| 95 | MPN identique |
| 92 | SKU constructeur identique |
| 90 | Référence constructeur identique |
| 85 | Nom normalisé + marque |
| 80 | Nom proche + date de sortie |
| 75 | Nom proche + collection |
| 70 | Nom normalisé seul |

Au-dessus de `merge_threshold` (90 par défaut), la fiche rejoint le produit
existant et l'enrichit. En dessous — mais au-dessus de `suggestion_floor` —
elle crée un nouveau produit **et** une suggestion de fusion dans le
dashboard : **rien n'est jamais fusionné à tort en silence**.

Les identifiants sont validés (clé de contrôle GTIN) et normalisés : un
UPC-A américain devient l'EAN-13 équivalent, donc un produit trouvé chez
deux marchands de continents différents se rejoint bien. Les codes bidons
(`0000000000000`) sont refusés — les accepter fusionnerait à confiance 100
tous les produits qui les portent.

### Enrichissement

Les plugins extraient ce que les marchands publient déjà pour les moteurs
de recherche — **JSON-LD schema.org**, microdata, balises meta : EAN, UPC,
SKU, MPN, marque, date de sortie, image. Aucune connaissance des sites
n'est nécessaire. Chaque marchand complète les trous laissés par les
autres, sans jamais écraser une valeur déjà connue.

### Ajouter une méthode de corrélation (OCR, embeddings, similarité visuelle…)

Une classe, une entrée dans la liste — le moteur ne change pas :

```python
class VisualSimilarityStrategy:
    name = "visual_similarity"
    score = 88

    async def find(self, draft, candidates):
        ...  # OCR, embeddings, recherche inversée d'image…

engine = MatchingEngine([*default_strategies(), VisualSimilarityStrategy()])
```

La stratégie est une coroutine : elle peut appeler un service distant ou un
modèle sans bloquer le reste.

### Identité produit et recherche multi-clés

Chaque information découverte devient **immédiatement une clé de recherche
chez tous les autres marchands**. Le profil d'identité rassemble EAN, UPC,
ISBN, GTIN, ASIN, SKU, MPN, référence constructeur, numéro de modèle,
marque, fabricant, collection, édition, date de sortie, nom canonique,
alias et images — **chaque champ portant sa confiance et sa source**.

Amazon publie un UPC ? Le moteur construit aussitôt la liste des
recherches, de la plus discriminante à la plus vague :

```
 98  upc=196214141612
 93  asin=B0H3PRH89L
 92  mpn=10-10410-102
 80  brand_model=Pokémon 10-10410-102
 70  canonical_name=Pokémon Premiers Partenaires Série 3
```

Chaque plugin reçoit l'identité **et** la clé à essayer, puis choisit seul
sa méthode (API, recherche interne, sitemap, RSS, HTTP, Playwright). Il
rend des candidats motivés — confiance, champs concordants, raison — et
s'arrête dès qu'un résultat dépasse `stop_confidence`.

### Mémoire des recherches et relance automatique

**Un échec n'est jamais perdu.** Si Micromania ne connaît pas encore cet
UPC aujourd'hui, la tentative est enregistrée avec une heure de relance.
Le moteur y revient tout seul — 30 min, puis 45 min, 1 h 07… plafonné à
6 h — jusqu'à ce que la fiche apparaisse.

C'est décisif pour les drops, où les pages sortent progressivement selon
les enseignes : la fiche publiée trois heures après les autres est repérée
sans repartir de zéro.

La page Catalogue expose, pour chaque produit, la section **Identité**
(valeurs, confiances, sources, alias, clés générées) et l'historique des
**recherches inter-sites** : quel marchand, quelle clé, quel résultat, et
l'heure de la prochaine relance.

Activation : `intelligence.cross_site_search` dans
[config/discovery.yaml](config/discovery.yaml).

### Ajouter une méthode d'identification (OCR, code-barres, CLIP, LLM…)

Une classe déposée dans `src/intelligence/strategies/` ou
`plugins/<site>/identity.py` — elle est **découverte automatiquement** :

```python
class BarcodeStrategy:
    name = "barcode_ocr"
    priority = 95

    async def enrich(self, identity, context):
        # context porte url, titre, html, image_url
        return identity.with_field("ean", decoded, 88, "barcode_ocr")
```

Aucune modification du moteur. Une stratégie en échec est journalisée et
ignorée : l'identité déjà acquise n'est jamais perdue.

### Page Catalogue

Une ligne par **produit**, dépliable sur toutes ses offres : marchand,
disponibilité, prix, état, dernière vérification — la meilleure offre est
mise en avant. Le champ `group` des produits surveillés devient
automatique : c'est l'UUID du produit canonique.

## Découverte automatique des produits

Le moteur ne se contente plus de surveiller des URL connues : il peut
**trouver lui-même les nouvelles fiches** publiées sur un site.

Deux familles de plugins, totalement indépendantes :

| Famille | Fichier | Rôle |
|---|---|---|
| **Monitor** | `plugins/<site>/monitor.py` | Surveille UNE URL connue |
| **Discovery** | `plugins/<site>/discovery.py` | Explore le site et repère les fiches |

Un site peut n'avoir que l'un, que l'autre, ou les deux. Le cœur ne connaît
que ces deux interfaces — ajouter Amazon, Fnac ou Cultura ne demande
**aucune modification du moteur**.

### Fonctionnement

À chaque cycle (`scan_interval`), chaque plugin explore son site et rend
une liste de fiches. Pour chacune, le moteur calcule une **empreinte
stable** (EAN, sinon SKU, sinon URL canonique — paramètres de tracking
retirés) : une fiche déjà connue n'est jamais redécouverte. Les nouvelles
passent par les **règles configurables**, puis par le **mode
d'approbation** :

| Mode | Comportement |
|---|---|
| `auto` | tout ce qui n'est pas exclu est importé et surveillé aussitôt |
| `review` | tout arrive dans la page **Découverte** pour validation manuelle |
| `rules` | seules les fiches correspondant aux règles sont importées |

L'exclusion s'applique dans **tous** les modes. Un produit importé est
surveillé en quelques secondes, **sans redémarrage**.

Tout se règle dans [config/discovery.yaml](config/discovery.yaml)
(désactivé par défaut).

### Stratégies d'exploration

Chaque plugin choisit la sienne — le moteur n'en sait rien : HTTP, sitemap,
Playwright, API, RSS. Deux outils génériques sont fournis
(`src/discovery/strategies.py`) :

- **sitemap** : suit `robots.txt` → `sitemap.xml`. Ne demande que l'URL
  racine du site : **aucune URL de fiche à connaître**. C'est le mode par
  défaut du plugin Micromania.
- **listings** : analyse des pages de catégorie / nouveautés / précommandes
  dont les URL sont renseignées dans la configuration, avec rendu navigateur
  optionnel.

### Page Découverte

Miniature, date, site, titre, URL, statut et motif de la décision, avec
trois actions : **Ajouter à la surveillance**, **Ignorer**, **Toujours
ignorer** (décision durable). Les nouvelles fiches apparaissent en temps
réel par WebSocket, et une alerte Telegram « 🆕 Nouveau produit détecté »
est envoyée.

## Diagnostiquer un statut « unknown »

Chaque analyse écrit une ligne de diagnostic (niveau `CHECK` : visible dans
`logs/drop-monitor.log` et dans la page **Logs** du dashboard) :

```
[CHECK] Analyse micromania — Pokémon 30 Ans UPC : html=145.2 Ko, texte=8421 car.,
        titre=« … », boutons candidats=17 [Tout accepter, Précommander, …],
        retenus=['Précommander'], mots-clés=['precommander (bouton)']
        → statut=preorder, prix=189,99 €
```

Quand le statut reste `unknown`, une erreur explicite indique quoi faire.
Les trois causes possibles, dans l'ordre de fréquence :

1. **Fiche rendue en JavaScript** — le HTML statique ne contient pas encore
   le bouton d'achat. Le rendu navigateur (ci-dessous) prend le relais
   automatiquement.
2. **Page d'attente anti-robot** servie en HTTP 200 (Cloudflare, DataDome,
   Imperva…) — détectée et nommée explicitement dans les logs.
3. **Vocabulaire différent** — le bouton existe mais son libellé n'est pas
   dans les mots-clés. Les « boutons candidats » du log listent ce que la
   page contient réellement : il suffit d'ajouter le libellé manquant dans
   `plugins/<site>/keywords.py`.

La comparaison est insensible aux accents, à la casse et aux espaces
insécables : « PRECOMMANDER », « Précommander » et « Ajouter&nbsp;au&nbsp;panier »
sont tous reconnus.

## Rendu navigateur de secours (403 et pages JavaScript)

Chaque vérification tente d'abord une requête HTTP (rapide, ~200 ms). Elle
bascule automatiquement sur Chromium dans deux cas :

- le site répond **403 / 401 / 429 / 503** (page refusée à un client non-navigateur) ;
- la page est bien reçue mais **l'analyse reste inconclusive** (`unknown`).

Le HTML obtenu après exécution du JavaScript est alors ré-analysé. Le
navigateur est **le même que celui des captures d'écran** — aucun coût
supplémentaire au démarrage — et les rendus simultanés sont bornés par
`BROWSER_FALLBACK_MAX_CONCURRENT`.

Si le HTML statique suffit, aucun navigateur n'est lancé : le surcoût
n'existe que là où il est nécessaire.

Pour un site dont les fiches sont *toujours* rendues côté client, passer
`requires_javascript = True` dans son `monitor.py` évite la requête HTTP
inutile à chaque cycle.

> ⚠️ **Limite à connaître.** Ce rendu affiche la page comme un navigateur
> ordinaire ; il ne contourne aucune protection. Si un site refuse une
> **plage d'adresses IP** (typiquement celles des hébergeurs), Chromium
> recevra le même 403 depuis cette IP. Voir « HTTP 403 en production »
> dans [DEPLOY.md](DEPLOY.md).

### Plugin Amazon — la référence

`plugins/amazon/` est le modèle de tous les futurs marchands. Il montre
qu'un plugin peut être bien plus qu'une liste de mots-clés :

```
plugins/amazon/
├── __init__.py     METADATA + exports
├── keywords.py     vocabulaire par état, sans aucune logique
├── parser.py       enum AmazonState, buy box, URL canonique
├── monitor.py      surveillance d'une fiche connue
├── identity.py     stratégie d'identité (auto-découverte)
└── discovery.py    exploration + recherche par identité
```

**États natifs.** Une vraie enum (`AVAILABLE`, `INVITATION`, `PREORDER`,
`COMING_SOON`, `OUT_OF_STOCK`, `UNAVAILABLE`, `UNKNOWN`) traduite vers le
vocabulaire du cœur. Les états sont évalués du plus spécifique au plus
général, et une mention de rupture l'emporte sur un bouton résiduel.
La comparaison ignore casse, accents et espaces insécables.

**Buy box.** Prix, devise, vendeur, expéditeur, note de stock, variation,
édition et présence de la buy box — exposés dans `details`. Le vendeur est
volontairement **hors du hash** : Amazon fait tourner ses marchands, cela
déclencherait des alertes sans rapport avec la disponibilité.

**URL.** `/dp/`, `/gp/product/`, `/gp/aw/d/`, liens affiliés (`?tag=`),
sponsorisés, `ref=`, `utm_*`, ancres — tout est ramené à
`https://www.amazon.fr/dp/<ASIN>` avant la première requête.

**Identité.** ASIN (URL ou HTML), UPC/EAN/GTIN, MPN, numéro de modèle,
marque, fabricant, collection, édition, date de sortie — lus dans les
données structurées *et* dans le tableau de caractéristiques Amazon, avec
conversion UPC-A → EAN-13 et dates au format ISO.

**Recherche.** `search(identity, ctx, key)` : accès direct par ASIN,
recherche du code pour EAN/UPC/MPN, recherche texte pondérée par la
ressemblance du titre pour les clés faibles. Le moteur ne connaît aucune
URL Amazon.

**Playwright.** HTTP d'abord, toujours. Le navigateur n'est ouvert que si
la réponse est inexploitable — page d'interception détectée ou HTML trop
maigre. Une fiche lisible en HTTP n'ouvre jamais Chromium.

> Amazon protège fortement ses pages. Le plugin se contente d'un accès
> ordinaire et ne contourne aucune protection : une interception rend
> « aucun résultat », que le moteur retentera plus tard.

### Ajouter un nouveau site (architecture par plugins)

Chaque site est un **plugin totalement indépendant** dans `plugins/`,
découvert automatiquement au démarrage. Un plugin défectueux est ignoré
sans jamais affecter les autres sites ni le cœur de l'application.

1. Créer le dossier `plugins/fnac/` sur le modèle de `plugins/micromania/` :

   ```
   plugins/fnac/
   ├── __init__.py      # expose la classe monitor
   ├── metadata.py      # identité du plugin (nom, version, URL de base)
   ├── keywords.py      # mots-clés propres au site
   ├── selectors.py     # sélecteurs CSS propres au site
   ├── parser.py        # analyse HTML spécifique (optionnelle)
   └── monitor.py       # la classe monitor
   ```

2. Dans `monitor.py`, hériter de l'analyse générique :

   ```python
   from typing import ClassVar
   from src.monitors.generic import GenericHtmlMonitor
   from .metadata import METADATA

   class FnacMonitor(GenericHtmlMonitor):
       site_name: ClassVar[str] = METADATA.site_name
       display_name: ClassVar[str] = METADATA.display_name
   ```

3. Utiliser `site: fnac` dans le YAML. **Aucune modification du cœur** :
   le plugin est découvert et chargé automatiquement.

Si le HTML du site change un jour, seuls `keywords.py` / `selectors.py`
(ou `parser.py`) de CE plugin sont à ajuster.

### Surveiller un même produit sur plusieurs sites

Donner le même `group` aux entrées concernées :

```yaml
  - name: "Pokémon UPC Jour — Micromania"
    site: micromania
    group: pokemon-30-upc-jour
    ...
  - name: "Pokémon UPC Jour — Fnac"
    site: fnac
    group: pokemon-30-upc-jour
    ...
```

Ce regroupement alimentera le tableau comparatif multi-sites du dashboard.

### Notifier plusieurs destinations Telegram

Dans `.env`, ajouter les Chat IDs supplémentaires (groupe privé, second
compte…) séparés par des virgules — chacun reçoit la même alerte :

```
TELEGRAM_CHAT_ID=1267117266
TELEGRAM_CHAT_IDS=-100123456789,987654321
```

### Ajouter un canal de notification (Discord, Email, SMS…)

Créer une classe héritant de `BaseNotifier` dans `src/notifications/`, puis
l'enregistrer via `notifications.register(...)` dans `main.py`.

## Serveur web & API (v1)

Le serveur héberge l'API REST **et** le moteur de surveillance (une seule
application, un seul process — idéal Railway) :

```bash
python server.py
```

- **Swagger** : http://localhost:8000/api/docs (ReDoc sur `/api/redoc`)
- **Healthcheck public** : `GET /api/v1/health`
- **Connexion** : `POST /api/v1/auth/login` avec `DASHBOARD_USERNAME` /
  `DASHBOARD_PASSWORD` (`.env`) → cookie httpOnly signé (`SECRET_KEY`)
- Toutes les autres routes exigent la session (ou `Authorization: Bearer`).

Principales ressources (toutes paginées : `page`, `page_size`, `sort`, `order`,
enveloppe `{items, total, page, page_size, pages}`) :

| Ressource | Routes |
|---|---|
| Produits | `GET/POST /api/v1/products`, `GET/PATCH/DELETE /api/v1/products/{uuid}`, `POST /api/v1/products/{uuid}/check`, `GET /api/v1/products/{uuid}/timeline` |
| Alertes | `GET /api/v1/alerts` (filtres site, type, produit, notified) |
| Timeline | `GET /api/v1/timeline` |
| Checks | `GET /api/v1/checks` |
| Logs | `GET /api/v1/logs` (niveau, recherche) |
| Stats | `GET /api/v1/stats/overview` |
| Monitors | `GET /api/v1/monitors` |
| Paramètres | `GET /api/v1/settings`, `GET /api/v1/settings/telegram/status`, `POST /api/v1/settings/telegram/test` |
| Santé | `GET /api/v1/health` (public), `GET /api/v1/health/system` |

Le CRUD produits est appliqué **à chaud** par le moteur (réconciliation
toutes les 5 s) : aucun redémarrage, jamais.

### Temps réel (WebSocket)

Un canal unique multiplexé : `ws://…/api/v1/ws` (cookie de session, ou
`?token=<jwt>` hors navigateur ; socket refusé → code 4401).

Enveloppe commune `{"type", "payload", "ts"}` ; types émis :

| Type | Contenu |
|---|---|
| `hello` | connexion acceptée (premier message, toujours) |
| `check` | un check terminé (produit, statut, dispo, temps de réponse) |
| `timeline` | nouvel événement de timeline (baseline, changement…) |
| `alert` | changement notifiable — émis AVANT l'envoi Telegram |
| `screenshot` | capture terminée (`alert_id`, chemin) → la miniature apparaît |
| `alert_status` | résultat de l'envoi (`alert_id`, `delivered`) |
| `engine` | moteur démarré / arrêté |
| `log` | nouvelle ligne de log |
| `ping` / `pong` | heartbeat (30 s ; le client peut envoyer `ping`) |

## Captures d'écran (Playwright)

Lorsqu'un événement **important** est détecté (ouverture des précommandes,
retour en stock, apparition d'un prix ou d'un bouton d'achat, changement de
statut), une capture de la fiche produit est réalisée et **jointe à l'alerte
Telegram** (`sendPhoto`, le message devient la légende).

Installation :

```bash
pip install playwright
playwright install chromium
```

Fonctionnement :

- **Le moteur n'attend jamais Playwright.** Le service est un abonné du bus
  qui enfile la demande et rend la main instantanément ; des workers bornés
  (`SCREENSHOT_MAX_CONCURRENT`) traitent la file.
- Chromium est **lancé une seule fois** puis réutilisé ; chaque capture
  obtient un contexte isolé, fermé juste après.
- Les bandeaux cookies (OneTrust, Didomi, Cookiebot, Axeptio, Usercentrics…)
  sont fermés automatiquement, avec repli sur les boutons « Tout accepter » /
  « Accept all », puis masquage CSS. **Un échec ne bloque jamais la capture.**
- Animations et curseurs sont neutralisés, avec un court délai de
  stabilisation, pour une image nette et reproductible.
- Un check détectant plusieurs changements simultanés (prix **et**
  précommande) ne produit **qu'une seule capture**, partagée par les alertes.
- Si Playwright échoue, dépasse son délai ou n'est pas installé, **l'alerte
  part quand même** en texte seul — aucune alerte n'est perdue.

Fichiers : `<DATA_DIR>/screenshots/YYYY-MM-DD/site_produit_YYYY-MM-DD_HH-mm-ss.png`
(la base ne stocke que le chemin relatif ; purge automatique au démarrage
selon `SCREENSHOT_RETENTION_DAYS`).

Dans le dashboard, chaque alerte affiche sa miniature — clic pour agrandir,
avec téléchargement et lien vers la fiche.

Réglages : voir le bloc « Captures d'écran » de [.env.example](.env.example).

**Évolutions prévues** (ossature en place dans `capture.py` : `CaptureRequest`
porte déjà `artifact` et `clip`) : génération de PDF, captures multiples d'une
même page, capture d'une zone précise, comparaison de deux captures, GIF
d'évolution.

## Dashboard web (React + PWA)

Le frontend vit dans `frontend/` (Vite + React + TypeScript + Tailwind v4 +
Lucide + Recharts, thème sombre) :

```bash
cd frontend
npm install
npm run build     # → frontend/dist, servi automatiquement par FastAPI
```

Puis `python server.py` et ouvrir http://localhost:8000 — connexion avec
`DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`.

En développement : `npm run dev` (port 5173, proxy API + WebSocket vers :8000).

Pages : Dashboard (état global, graphiques, cartes produits, activité),
Produits (CRUD à chaud), Alertes, Activité (timeline), Logs (direct),
Monitors, Paramètres (diagnostic Telegram), Santé.

**PWA** : manifest + icônes + service worker minimal (`frontend/public/sw.js`)
— installable, offline minimal (le shell est mis en cache, l'API jamais).
Points d'extension prévus : notifications push navigateur
(`src/pwa/registerSW.ts`), pré-cache enrichi.

**Architecture front** : `components/ui/` (composants 100 % génériques,
sans logique métier), `components/domain/` (présentation produit),
`pages/` (assemblage), `api/` (client + types miroirs du contrat v1),
`ws/` (WebSocket avec reconnexion automatique).

## Lancement (mode CLI, sans interface web)

```bash
python main.py
```

Sortie console :

```
14:03:01 [INFO] Canal de notification actif : telegram
14:03:01 [INFO] Micromania lancé — Pokémon 30 Ans ETB (toutes les 60 s)
14:03:01 [CHECK] Vérification : Pokémon 30 Ans ETB (micromania)
14:03:02 [INFO] Baseline enregistrée : Pokémon 30 Ans ETB (statut : unavailable)
14:04:03 [CHECK] Vérification : Pokémon 30 Ans ETB (micromania)
14:04:04 [ALERTE] Pokémon 30 Ans ETB — preorder_opened : unavailable → preorder
```

Arrêt : `Ctrl+C`.

## Logs

Créés automatiquement dans `logs/` :

| Fichier            | Contenu                                  |
|--------------------|------------------------------------------|
| `drop-monitor.log` | Tout (checks, infos, alertes, erreurs)   |
| `alerts.log`       | Uniquement les changements détectés      |
| `errors.log`       | Uniquement les erreurs (réseau, Telegram)|

L'état mémorisé de chaque produit est dans `data/state/*.json` — le supprimer
force une nouvelle baseline (sans alerte).

## Robustesse

- **Timeout** configurable (`defaults.request_timeout` dans le YAML).
- **Retries** avec backoff exponentiel (`max_retries`, `retry_backoff`).
- **Jitter** de ±10 % sur les intervalles pour ne pas marteler les sites.
- Intervalle minimal de **10 secondes** imposé par la validation.
- Une erreur sur un produit n'affecte jamais les autres (une boucle par produit).

## Déploiement (Docker / Railway)

Tout est décrit dans **[DEPLOY.md](DEPLOY.md)** : volume de persistance,
variables d'environnement, healthcheck, mémoire et captures, diagnostic.

En résumé, en local :

```bash
docker compose up -d --build
```

Sur Railway : déployer depuis GitHub (le `Dockerfile` et `railway.json` sont
détectés automatiquement), **monter un volume sur `/data`**, renseigner les
variables, et conserver **une seule instance** (`numReplicas: 1`).

## Tests

```bash
pytest
```

Les tests Playwright réels (Chromium) sont inclus ; pour les ignorer :

```bash
pytest --ignore=tests/test_screenshot_playwright.py
```
