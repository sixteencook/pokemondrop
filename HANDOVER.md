# Drop Monitor — état du projet (V1)

> Document de reprise. À lire en entier avant de toucher au code dans une
> nouvelle session. Il décrit ce qui existe, **pourquoi** c'est fait ainsi,
> les pièges rencontrés, et ce qui reste ouvert.
>
> Objectif : comprendre le projet en moins de 30 minutes.

**État au 3 août 2026 — version 1.0.** 481 tests passent, 126 fichiers
Python, 48 fichiers TypeScript, 2 plugins de marchand (+ le monitor
générique), 13 tables SQLite, schéma en version 5.

---

## 1. Ce qu'est le projet

Une **plateforme de veille e-commerce orientée produit**. Point de départ :
être alerté sur Telegram dès qu'une précommande Pokémon ouvre chez
Micromania. Point d'arrivée : un logiciel qui découvre lui-même les fiches,
comprend que plusieurs URL désignent le même produit, les surveille toutes,
explique chacune de ses décisions et **surveille sa propre santé**.

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

## 3. L'idée centrale : on ne surveille pas du HTML

C'est **le** concept à comprendre avant tout le reste.

Le projet a longtemps comparé des listes de boutons et des hachages de
texte. Deux conséquences, toutes deux observées en production :

- **faux positifs** — Amazon change un bandeau Prime, une mention de
  livraison ou l'ordre d'un carrousel, le hash bouge, une alerte part alors
  que rien n'a changé pour l'acheteur ;
- **oscillation** — une lecture partielle rend l'analyse inconclusive,
  l'état retombe à « inconnu », puis remonte au cycle suivant : deux
  alertes pour un produit parfaitement immobile.

Le HTML n'est qu'une **source de données**. Ce que l'on surveille est un
**état métier** : *que peut faire l'acheteur, à quel prix, chez qui ?*

### `OfferState` — `src/models/offer.py`

Chaque plugin a une seule obligation : traduire sa page en un `OfferState`.

| Champ | Sens |
|---|---|
| `action` | l'**unique** action d'achat principale (`PurchaseAction`) |
| `native_state` | état natif du marchand (`invitation`, `third_party_only`…) |
| `has_buy_box` | un contrôle d'achat existe |
| `seller_type` | `official` / `third_party` / `unknown` |
| `seller_name` | nom du vendeur |
| `price`, `currency` | prix normalisé |
| `identifier` | ASIN, SKU, référence marchand |
| `scope_version` | version des règles d'analyse |

`PurchaseAction` est fermée et générique : `add_to_cart`, `buy_now`,
`preorder`, `request_invite`, `notify_me`, `coming_soon`,
`temporarily_unavailable`, `currently_unavailable`, `discontinued`,
`third_party_only`, `none`. La disponibilité du cœur **découle** de
l'action — il n'y a qu'une source de vérité.

### Le hash métier

`OfferState.business_hash()` empreinte les champs ci-dessus et **rien
d'autre** : ni libellé de bouton, ni texte, ni DOM. Amazon peut refondre
son interface sans qu'il bouge d'un caractère.

Deux subtilités volontaires :

- **`seller_name` n'entre dans le hash que pour le vendeur officiel.** Une
  place de marché fait tourner ses revendeurs tiers en permanence ; y
  réagir produirait un flot d'alertes sans intérêt. Le *type* de vendeur,
  lui, compte toujours.
- **`scope_version`** invalide délibérément tous les états mémorisés quand
  les règles d'analyse évoluent : un changement de parseur ne doit jamais
  se lire comme un changement du produit. Le cycle suivant rejoue une
  baseline silencieuse, sans rafale d'alertes.

---

## 4. Architecture

Un seul processus : FastAPI héberge l'API, le WebSocket, le dashboard
compilé **et** les moteurs, dans la même boucle asyncio. Pas de Redis, pas
de worker séparé. C'est ce qui rend le déploiement Railway trivial.

```
Découverte ──┐
             ├──▶ Event Bus ──┬──▶ SQLite (checks, timeline, alertes, incidents)
Surveillance ┘                ├──▶ Captures Playwright
                              ├──▶ WebSocket (dashboard temps réel)
                              └──▶ Telegram
Product Intelligence ─────────┘
```

### Flux d'un cycle de surveillance

```
prepare_request()      le plugin décrit la localisation voulue
        ↓
   fetch HTTP          403/429 → bascule Chromium (statut d'origine conservé)
        ↓
    parse()            → OfferState + CheckDiagnostics
        ↓
 detect_changes()      compare l'état métier, jamais le HTML
        ↓
  confirmation         relit la page, rejoue la détection, compare les
                       ÉVÉNEMENTS obtenus (pas les pages)
        ↓
  publication bus      CHECK_COMPLETED / CHANGE_DETECTED
        ↓
 recorder → SQLite → captures → WebSocket → Telegram
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
| `src/models/` | `OfferState`, `ProductSnapshot`, `CheckDiagnostics`, événements |
| `src/core/` | moteur de surveillance, détecteur métier, event bus, preuves |
| `src/monitors/` | contrat plugin, `RequestPlan`, parser générique, registre |
| `src/discovery/` | découverte automatique : plugins, règles, fingerprint |
| `src/intelligence/` | produits canoniques, offres, matching, identité, recherche inter-sites |
| `src/db/` + `src/repositories/` | SQLAlchemy async + couche Repository |
| `src/services/` | recorder, captures, stats, **santé**, **histoire produit** |
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

Deux garanties structurantes :

- **Mémoire métier.** Une lecture non concluante ne produit aucun événement
  **et n'écrase pas** le dernier état connu. C'est ce qui supprime
  l'oscillation « invitation → inconnu → invitation ».
- **Confirmation par re-détection.** Un changement n'est notifié que si une
  seconde lecture produit **exactement les mêmes événements métier**.
  Comparer des signatures d'événements plutôt que des pages évite qu'une
  variation cosmétique fasse échouer la confirmation.

Baseline silencieuse au premier passage — jamais d'alerte sur la première
observation.

### Discovery Engine (`src/discovery/engine.py`)

Explore les sites, calcule une **empreinte stable** par fiche (EAN > SKU >
URL canonique, paramètres de tracking retirés), applique des règles
configurables, puis selon le mode : `auto` / `review` / `rules`.

Trois stratégies fournies et génériques : sitemap (robots.txt →
sitemap.xml), pages de listing, recherche. Chaque balayage porte sa durée
(`ScanReport.duration_ms`), suivie sur la page Santé.

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

## 6. Décision d'un plugin : l'action d'achat principale

Une fiche Amazon porte des dizaines de contrôles. **Un seul** décrit ce que
l'acheteur peut faire du produit. `plugins/amazon/actions.py` en rend
exactement un, et dit toujours d'où il vient.

**Ordre de résolution**, du plus fiable au moins fiable :

1. **Mention bloquante** du bloc `#availability` — Amazon laisse
   fréquemment le bouton d'achat en place sur une fiche en rupture ; le
   croire produirait un faux retour en stock.
2. **Contrôle d'achat identifié** (`PURCHASE_CONTROLS`), libellé réel.
3. **Contrôle non exclu dont le libellé nomme l'action** — filet de
   sécurité : Amazon renomme ses identifiants régulièrement.
4. **Mention positive** (`En stock`, `bientôt disponible`).
5. **Texte du périmètre**, marqueurs forts seuls.
6. **Buy box sans libellé lisible** → mise au panier.

**Liste d'exclusion formelle** : liste d'envies, adresse de livraison,
Prime, financement, garantie, partage, comparaison, avis, questions,
sponsorisés, renvoi vers les autres vendeurs. Un contrôle exclu ne peut
**jamais** décider, quelle que soit l'étape. Chaque exclusion porte son
motif, affiché dans les logs (« 22 ignorés — wishlist, livraison, Prime »).

⚠️ **Piège corrigé, à ne pas réintroduire** : « date de sortie » était
classé `COMING_SOON`. Cette mention figure sur *toute* fiche de
précommande, y compris quand le bouton « Précommander » est actif — elle
masquait donc l'ouverture d'une précommande, le faux négatif le plus
coûteux du projet. Un contrôle d'achat vivant l'emporte désormais toujours
sur une annonce de sortie. Verrouillé par test.

---

## 7. Contexte France (Amazon)

Amazon ne sert pas la même page selon la langue et le **pays de livraison**
de la session. Un ASIN affiché « Demande d'invitation » depuis la France
apparaît « Actuellement indisponible » si Amazon croit livrer aux
États-Unis : l'offre n'est pas proposée à cette destination.

`plugins/amazon/marketplace.py` fait deux choses :

1. **Demander** la version française avec livraison France :
   `?language=fr_FR`, cookies `lc-acbfr` et `i18n-prefs`, en-tête
   `Accept-Language`, et — en cas de bascule Chromium — locale `fr-FR` et
   fuseau `Europe/Paris` **avec les cookies** dans le contexte navigateur.
2. **Constater** ce qui a été servi : marketplace, langue, pays de
   livraison (lu dans le « glow »).

**Si la page a été servie pour un autre pays, aucun état n'est retenu** —
ni négatif, ni positif. Annoncer une invitation ouverte serait aussi faux
qu'annoncer une rupture. L'état devient `UNKNOWN`, le moteur garde le
dernier état métier connu, et le log dit précisément quel sélecteur a
détecté quel pays.

Une URL pointant explicitement vers `amazon.de` est **respectée**, jamais
réécrite : la règle « ne jamais inventer d'URL produit » vaut ici aussi.

---

## 8. Événements métier

Le détecteur (`src/core/detector.py`) ne compare que l'état métier. Quatre
règles, dans cet ordre :

1. **Baseline silencieuse** au premier passage.
2. **Une lecture non concluante n'est jamais un événement**, ni comme
   ancien état ni comme nouveau.
3. **Seul l'état métier est comparé** — ni boutons, ni texte, ni hash.
4. **Un changement, un événement** : une transition de disponibilité
   absorbe l'apparition de prix qui l'accompagne.

| Événement | Libellé |
|---|---|
| `product_appeared` | Produit découvert |
| `product_delisted` | Fiche retirée |
| `price_appeared` / `price_changed` | Prix détecté / modifié |
| `preorder_opened` | Précommande ouverte |
| `invitation_opened` | Invitation ouverte |
| `back_in_stock` | Retour en stock |
| `went_out_of_stock` | Rupture de stock |
| `seller_became_official` | *Amazon* devient vendeur |
| `seller_left_buybox` | *Amazon* quitte la Buy Box |
| `status_changed` | Disponibilité modifiée |

`button_changed` et `page_changed` **ne sont plus jamais émis**. Ils
décrivaient le HTML, pas le produit. Ils subsistent dans l'énumération
uniquement pour relire les lignes déjà écrites en base
(`RETIRED_CHANGE_TYPES`, verrouillé par test).

---

## 9. Observabilité

### Le principe de coût

**Rien n'est produit spécialement pour la page Santé.** La table `checks`
est déjà écrite une fois par cycle ; trois colonnes nullables y ont été
ajoutées — `fetch_source`, `http_status`, `confidence` — qui voyagent dans
une écriture qui a lieu de toute façon. Elles suffisent au taux de bascule
navigateur, à la distribution HTTP et à la tendance de confiance.

`engine_events` ne reçoit **que les incidents et les phases mesurées**. Un
cycle nominal n'y écrit rien — verrouillé par test.

⚠️ Le statut HTTP conservé est celui du **refus initial** : un 403 suivi
d'un rendu Chromium réussi se lirait sinon comme un 200, et le signal le
plus important pour repérer un site qui commence à bloquer disparaîtrait.

### Health Score

Le score part de 100 et retire des pénalités **proportionnelles à un taux**
(jamais à un nombre brut : 10 erreurs sur 10 000 vérifications ne pèsent
pas comme 10 sur 100).

| Poste | Poids | Pénalité pleine à |
|---|---|---|
| Erreurs | 30 | 10 % |
| Blocages (403/429/captcha) | 25 | 5 % |
| États indéterminés | 20 | 20 % |
| Bascules navigateur | 10 | 30 % |
| Lenteur | 8 | 8 s |
| Confiance | 7 | < 50 % |

Total 100. En dessous de 10 vérifications, le plugin est « en observation » :
trois checks ne font pas une tendance. Le score global agrège les
composants, les plugins pesant 3× plus que Discovery et Intelligence.

### Auto-diagnostic

Compare chaque plugin **à son propre comportement** sur les 6 jours
précédents. Un plugin qui a toujours utilisé le navigateur n'est pas une
anomalie. Une dérive n'est signalée que si elle dépasse un plancher de 10 %
**et** double par rapport à la référence.

### Ce que la page Santé montre

Score système et par composant · anomalies détectées · une carte par plugin
(score, succès, 403/429/5xx, bascules, interceptions, états indéterminés,
confiance, dernière erreur) · Discovery · Product Intelligence · temps
moyen par étage (HTTP, navigateur, capture, balayage, corrélation) ·
**incidents** reconstitués en chaînes « 403 → bascule → issue » ·
historique technique · graphiques de dérive.

### Mode DEBUG

`PLUGIN_DEBUG=true` → `logs/debug.log`, un bloc par vérification : URL, URL
canonique, marketplace, pays, langue, bloc retenu **et motif de rejet des
autres**, sélecteur décisif, action principale, état métier, confiance,
prix, vendeur, buy box, hash métier, contrôles ignorés avec leur motif.

---

## 10. Histoire d'un produit

`src/services/product_story.py` fusionne monitoring, découverte et
intelligence en un seul récit, par **produit canonique** :

```
02 août   Amazon        Nouvelle fiche
03 août   Amazon        Invitation ouverte
05 août   Micromania    Nouvelle fiche
07 août   Micromania    Précommande ouverte
12 août   Amazon        Retour en stock
```

Il expose aussi la **propagation** (quel marchand publie ses fiches le plus
tôt, et avec quel retard), les **métriques métier** (marchands, premier
marchand, changements, notifications, captures, prix, précommandes,
invitations, retours en stock), l'**identité connue** et l'**historique des
recherches inter-sites** avec leur backoff.

Aucune écriture : trois lectures groupées puis une fusion en mémoire.

---

## 11. Base de données

13 tables, schéma **version 5**. `create_all` ne modifie jamais une table
existante : toute colonne ajoutée passe par `MIGRATIONS`
(`src/db/migrations.py`). Le mécanisme distingue base neuve et base en
service.

| Version | Contenu |
|---|---|
| 1 | schéma initial |
| 2 | Product Intelligence v2 (identité multi-clés) |
| 3 | archivage du HTML des alertes |
| 4 | observabilité : 3 colonnes sur `checks`, table `engine_events` |
| 5 | `engine_events.duration_ms` (temps par phase) |

Rétention : 30 jours pour `checks`, 14 jours pour `engine_events`, purgées
au démarrage.

---

## 12. Fiabilité — les garde-fous

Principe : **mieux vaut manquer une alerte qu'en produire une fausse.**

1. **Confirmation** — aucun changement notifié sur une seule lecture. La
   page est relue (4 s après) et la détection rejouée ; si les deux
   lectures ne produisent pas les mêmes événements, l'état précédent est
   conservé et **rien n'est envoyé**. Une lecture impossible n'est jamais
   une confirmation.
2. **Mémoire métier** — une lecture non concluante n'écrase rien.
3. **Aucun hash visible** — le hash est interne, les alertes montrent des
   libellés lisibles.
4. **Preuve archivée** — le HTML ayant motivé une alerte importante est
   conservé dans `<DATA_DIR>/evidence/`, servi en **texte brut**.
5. **Score de confiance** (Amazon) — 5 indices × 20 points. Sous 60,
   l'état devient `unknown`, rien n'est notifié.
6. **Nettoyage du bruit** — carrousels, recommandations, sponsorisés,
   cookies, newsletter, publicités, modales, avis, navigation et pied de
   page retirés **avant** toute mesure.
7. **Contradiction = abstention** (Micromania) — un bouton d'achat à côté
   d'une mention de rupture ne conclut rien.

Réglages : bloc `defaults` de `config/products.yaml`.

---

## 13. Bugs réels trouvés et corrigés

Chacun a été découvert en testant, pas en relisant.

| Bug | Conséquence évitée |
|---|---|
| **Jetons base64 dans les boutons** — lecture de la `value` de *tous* les `<input>` | Hash instable + alertes illisibles |
| **`0000000000000` passe la clé de contrôle GTIN** | Fusion à confiance 100 de tous les produits à EAN bidon |
| **Carrousel Micromania** — 145 boutons candidats | Fausse alerte « retour en stock » |
| **`attempts += 1` sur `None`** | Plantage à la première recherche inter-sites |
| **Candidat trouvé recréé en doublon** | Doublons dans le catalogue |
| **`create_app()` exécuté deux fois** | Application construite en double |
| **Chemins Windows en base** | Captures introuvables après déploiement Linux |
| **Import circulaire** (×4) | Résolus par `TYPE_CHECKING` ou remontée d'utilitaire |
| **Logs httpx exposaient le token Telegram** | Fuite du token dans `logs/` |
| **Accents non normalisés** | « PRECOMMANDER » jamais détecté |
| **Requête sans contexte de langue** | Amazon servait une page « livraison États-Unis » : le moteur lisait « indisponible » là où l'utilisateur voyait « Demande d'invitation » |
| **`date de sortie` classé COMING_SOON** | Ouverture de précommande manquée — le pire faux négatif |
| **403 masqué par le rendu Chromium** | Le signal « le site commence à bloquer » disparaissait |
| **404 renvoyait `UNKNOWN`** | « Fiche produit en ligne » ne pouvait plus jamais partir |
| **Transition vers `unknown` émise comme événement** | Oscillation invitation ↔ inconnu, deux alertes pour rien |

Trois fois, **le code avait raison et mes tests avaient tort** (dédup de
clés identiques, `model_number` plus discriminant que `brand+model`,
garde-fou GTIN sur le corps du code). Ne pas « corriger » ces
comportements sans relire les commentaires.

---

## 14. Décisions à connaître (et discutables)

- **`INVITATION` → `PREORDER`.** Une demande d'invitation signifie que le
  drop est lancé. Le libellé exact reste dans `action` et `native_state`.
- **Vendeur Amazon** : rotation entre revendeurs tiers = silence ;
  **Amazon → revendeur tiers = alerte** (`seller_left_buybox`).
- **Hors contexte France, aucun état n'est retenu** — y compris positif.
- **Restriction au conteneur produit désactivée par défaut** côté générique
  — deviner le bon conteneur a échoué deux fois sur du HTML Micromania
  réel. Reste activable via `product_scope_selectors`.
- **Une seule instance** (`numReplicas: 1`). Deux répliques = deux moteurs
  sur la même base SQLite : requêtes dupliquées, alertes en double.
- **Pas de `PATCH /settings`** — la configuration vit dans les variables
  d'environnement (bonne pratique Railway).
- **`/api/v1/health` reste public et minimal** (sonde Railway). La vue
  globale est à `/api/v1/health/overview`, protégée.

---

## 15. Limites connues

**Le blocage IP est le vrai obstacle.** Micromania et Amazon refusent
souvent le trafic des hébergeurs. La même URL répond 200 chez vous et 403
depuis Railway. Le rendu navigateur résout les 403 dus à l'absence de
JavaScript, **pas** un blocage par plage d'IP — Chromium sort par la même
adresse. Remède : héberger la surveillance à la maison (Raspberry Pi, NAS,
PC). Voir `DEPLOY.md`.

**Fonctionnalités livrées mais désactivées** :

| Fonction | Pourquoi désactivée |
|---|---|
| Découverte automatique | `config/discovery.yaml` → `enabled: false`. À activer en mode `review` d'abord. |
| Recherche inter-sites | Exige un `search_url_template` par marchand. Amazon a un défaut fonctionnel. |
| Listings Micromania | `listing_urls: []` — aucune URL de catégorie inventée. |

**Non réalisé** : Discord/Email/SMS (interfaces prêtes), PostgreSQL (couche
Repository prête, changer `DATABASE_URL`), notifications push PWA (points
d'extension dans `registerSW.ts` et `sw.js`).

**Résidu connu** : sur la fiche Micromania réelle, une bannière promo est
encore retenue comme bouton. Remède : un `product_scope_selectors` propre à
Micromania, à définir en observant le HTML.

**Écrire un plugin de découverte pour un marchand ne suffit pas** à peupler
la timeline canonique : il faut aussi que l'Intelligence corrèle ses offres.

---

## 16. Écrire un plugin

`plugins/amazon/` est **la référence**. Structure à copier :

```
plugins/<site>/
├── __init__.py     METADATA + exports
├── keywords.py     vocabulaire, aucune logique
├── actions.py      résolution de l'action d'achat + liste d'exclusion
├── marketplace.py  langue, pays, devise (si le site en dépend)
├── parser.py       états typés, URL canonique, OfferState
├── monitor.py      surveillance, prepare_request(), logs
├── identity.py     stratégie d'identité (auto-découverte)
└── discovery.py    exploration + search(identity, ctx, key)
```

Découverte **automatique** dans trois registres indépendants (monitors,
discovery, identity). Un plugin cassé est journalisé et ignoré — les autres
continuent. Tout est optionnel : un site peut n'avoir qu'un monitor.

- **Micromania** : hérite de `GenericHtmlMonitor` (mots-clés) — suffisant.
- **Amazon** : parser propre avec états typés et action principale. À
  suivre pour Fnac, Cultura, King Jouet.

Le contrat minimal : produire un `OfferState`. Tout le reste — détection,
confirmation, alertes, santé, timeline — fonctionne alors sans une ligne
supplémentaire.

---

## 17. Pièges de développement

- **`sqlite+aiosqlite:///:memory:`** donne une base **par connexion** du
  pool. Toujours utiliser une base fichier dans les tests
  (`tests/helpers.make_db`).
- **Défauts SQLAlchemy** appliqués au flush, pas à la construction.
- **`create_all` ne modifie jamais une table existante.** Ajouter une
  colonne = une entrée dans `MIGRATIONS`.
- **Les logs `CHECK` (niveau 15)** ne s'affichent pas en console par défaut
  mais vont dans `logs/` et dans la page Logs.
- **PowerShell** : pas de heredoc, pas de `sleep` enchaîné. Écrire les
  scripts multi-lignes dans un fichier temporaire.
- **Ne jamais inventer d'URL produit.**
- **Le caractère `→` n'existe pas en cp1252** : éviter dans les messages de
  log, la console Windows le refuse.

---

## 18. Par où continuer

La V1 est close. L'intention est de **laisser tourner plusieurs semaines**,
observer les comportements réels, et ne corriger que les bugs constatés.

Ensuite, par ordre de valeur :

1. **Activer la découverte en mode `review`** sur Micromania (sitemap) et
   observer une journée.
2. **Renseigner `search_url_template`** pour Micromania → débloque la
   recherche inter-sites.
3. **Héberger à la maison** si les 403 persistent sur Railway.
4. **Un `product_scope_selectors` Micromania** pour le résidu de bannière.
5. **V2 : nouveaux plugins** — Fnac, King Jouet, Cultura, Leclerc, Smyths,
   Carrefour. Chacun se copie sur `plugins/amazon/`.

Deux chantiers d'observabilité restent ouverts, sans urgence :

- les **compteurs** Discovery/Intelligence sont exacts, mais les recherches
  inter-sites n'écrivent pas encore d'`engine_events` : elles n'apparaissent
  donc pas dans l'historique technique ;
- aucun **test de charge** n'a été fait au-delà de quelques produits.

---

## 19. Documents

- `README.md` — installation, configuration, architecture, diagnostic
- `DEPLOY.md` — Docker, Railway, volume, blocage IP, dépannage
- `config/products.yaml` et `config/discovery.yaml` — abondamment commentés
- `/api/docs` — Swagger complet, en français
