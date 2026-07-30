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
