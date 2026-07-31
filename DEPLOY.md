# Déploiement — Drop Monitor

Deux cibles supportées : **Docker** (local ou serveur) et **Railway**.
Dans les deux cas, un seul conteneur héberge l'API, le dashboard, le moteur
de surveillance et les captures d'écran.

L'image pèse environ **1,9 Go** (Python 3.12 + Chromium + dashboard compilé)
et se construit en 5 à 10 minutes la première fois ; les reconstructions
suivantes ne rejouent que les couches applicatives (quelques secondes).

---

## 1. Docker (local ou serveur)

### Prérequis

Docker 24+ et Docker Compose v2.

### Lancement

```bash
cp .env.example .env
```

Renseigner au minimum dans `.env` :

| Variable | Rôle |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token du bot (@BotFather) |
| `TELEGRAM_CHAT_ID` | Destinataire principal |
| `DASHBOARD_USERNAME` | Identifiant de connexion au dashboard |
| `DASHBOARD_PASSWORD` | Mot de passe |
| `SECRET_KEY` | Clé de signature des sessions |

Générer une clé solide :

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Puis :

```bash
docker compose up -d --build
```

Le dashboard est sur http://localhost:8000, l'API sur `/api/docs`.

### Commandes utiles

```bash
docker compose logs -f
```

```bash
docker compose restart
```

```bash
docker compose down
```

Les données (base SQLite, captures, logs) survivent aux redémarrages et aux
reconstructions : elles vivent dans le volume nommé `drop-monitor-data`.
`docker compose down -v` les supprimerait — à n'utiliser que volontairement.

### Sauvegarde

```bash
docker run --rm -v drop-monitor-data:/data -v ${PWD}:/backup alpine tar czf /backup/dropmon-backup.tar.gz -C /data .
```

---

## 2. Railway

### Étape 1 — Créer le service

1. Pousser le projet sur un dépôt GitHub.
2. Sur Railway : **New Project → Deploy from GitHub repo**.
3. Railway détecte `railway.json` et construit à partir du `Dockerfile`.

### Étape 2 — Ajouter le volume (indispensable)

**Sans volume, la base et les captures sont perdues à chaque déploiement.**

Dans le service : **Settings → Volumes → New Volume**, point de montage :

```
/data
```

Le conteneur y écrit déjà `drop_monitor.db`, `screenshots/` et `logs/`.

### Étape 3 — Variables d'environnement

Dans **Variables**, définir :

| Variable | Valeur | Obligatoire |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | votre token | ✅ |
| `TELEGRAM_CHAT_ID` | votre chat ID | ✅ |
| `TELEGRAM_CHAT_IDS` | destinataires supplémentaires, séparés par des virgules | — |
| `DASHBOARD_USERNAME` | identifiant | ✅ |
| `DASHBOARD_PASSWORD` | mot de passe fort | ✅ |
| `SECRET_KEY` | 64 caractères aléatoires | ✅ |
| `LOG_LEVEL` | `INFO` | — |
| `SCREENSHOTS_ENABLED` | `true` | — |
| `SCREENSHOT_MAX_CONCURRENT` | `1` (voir mémoire ci-dessous) | — |
| `SCREENSHOT_RETENTION_DAYS` | `90` | — |

`PORT`, `DATA_DIR`, `LOG_DIR` et `SCREENSHOTS_DIR` sont déjà positionnés par
l'image — ne les redéfinir que pour s'en écarter. `PORT` est injecté par
Railway et pris en compte automatiquement.

### Étape 4 — Vérifier

- **Healthcheck** : Railway sonde `/api/v1/health` (route publique, sans
  authentification) et redémarre le service en cas d'échec — c'est déjà
  configuré dans `railway.json`.
- **Logs** : tout est écrit sur stdout, donc visible dans l'onglet Deployments.
- **Redémarrage automatique** : `restartPolicyType: ON_FAILURE`, 10 tentatives.
- Ouvrir l'URL publique générée, se connecter, aller dans **Paramètres** :
  le bot Telegram et les captures doivent être annoncés « opérationnels ».

### ⚠️ Une seule instance

`numReplicas` est fixé à **1** et doit le rester. Deux instances
signifieraient deux moteurs de surveillance sur la même base SQLite :
requêtes dupliquées vers les sites, alertes en double et corruption possible.
Passer à l'échelle horizontalement demanderait d'abord de migrer vers
PostgreSQL (`DATABASE_URL`) et de séparer le moteur de l'API — l'architecture
le permet, mais ce n'est pas la configuration actuelle.

### Mémoire et captures

Chromium consomme environ **200 Mo par capture simultanée**, en plus des
~150 Mo de l'application. Recommandations :

| Mémoire du service | `SCREENSHOT_MAX_CONCURRENT` |
|---|---|
| 512 Mo | `1`, ou `SCREENSHOTS_ENABLED=false` |
| 1 Go | `1` (recommandé) |
| 2 Go et plus | `2` à `3` |

Si les captures échouent, elles n'empêchent jamais les alertes : celles-ci
partent alors en texte seul.

Chromium est lancé avec `--disable-dev-shm-usage` : il n'a donc pas besoin
d'un `/dev/shm` agrandi, ce que Railway ne permet pas de configurer. En
`docker compose`, `shm_size: 512mb` est tout de même fourni par confort.

---

## 3. Après le déploiement

1. Se connecter au dashboard.
2. **Paramètres → Envoyer une notification de test** pour valider Telegram
   de bout en bout.
3. **Produits** : renseigner les URL le jour où les fiches sont publiées,
   activer la surveillance. Les modifications sont prises en compte **à
   chaud**, sans redémarrage.

## 4. Diagnostic

Au démarrage, le conteneur affiche un résumé qui permet de vérifier la
configuration d'un coup d'œil :

```
[INFO] Drop Monitor v1.0.0 — Python 3.12.13, environnement « production », port 8000
[INFO] Stockage — base : sqlite · données : /data · captures : /data/screenshots · logs : /data/logs
```

Si `DATA_DIR` ne pointe pas vers un volume monté en production, une erreur
explicite est journalisée : **les données seraient perdues au prochain
déploiement**.

### HTTP 403 en production alors que tout fonctionne en local

C'est le cas le plus fréquent, et il n'est **pas** dû à l'application : de
nombreux sites marchands refusent le trafic venant des plages d'adresses
IP d'hébergeurs (Railway, AWS, OVH…), tout en acceptant une connexion
domestique. La même URL répond donc 200 chez vous et 403 depuis Railway.

Le rendu navigateur de secours est tenté automatiquement et résout les 403
liés à l'absence de JavaScript ou d'en-têtes de navigateur. **Mais si le
refus porte sur l'adresse IP, Chromium reçoit le même 403** : il sort par
la même IP.

Options, par ordre de simplicité :

1. **Héberger la surveillance chez vous** (Raspberry Pi, NAS, PC allumé) via
   `docker compose` : l'IP domestique n'est pas filtrée. C'est la solution la
   plus fiable pour ce type de site.
2. **Conserver Railway pour le dashboard** et faire tourner le moteur à la
   maison, les deux pointant vers la même base — nécessite de migrer vers
   PostgreSQL (`DATABASE_URL`), ce que la couche Repository permet déjà.
3. **Espacer les vérifications** (`check_interval` plus large) : certains
   filtrages sont déclenchés par la fréquence, pas par l'IP seule.

Le projet ne cherche pas à contourner ces protections — ni empreinte
navigateur falsifiée, ni rotation de proxys, ni résolution de CAPTCHA.
Un site qui refuse l'accès doit être respecté.

| Symptôme | Piste |
|---|---|
| Healthcheck en échec au démarrage | Consulter les logs : `SECRET_KEY`, volume `/data` non monté, ou port incorrect |
| HTTP 403 depuis Railway, 200 en local | Blocage par plage d'IP — voir la section ci-dessus |
| Statut bloqué sur `unknown` | Lire la ligne `[CHECK] Analyse …` dans les logs : elle liste les boutons réellement présents sur la page |
| « Chromium indisponible » dans Paramètres | Mémoire insuffisante — réduire `SCREENSHOT_MAX_CONCURRENT` ou désactiver les captures |
| Aucune alerte reçue | Vérifier le token/chat ID dans Paramètres, et qu'un produit est activé avec une URL |
| Données perdues après déploiement | Le volume n'est pas monté sur `/data` |
| Alertes en double | Plus d'une instance : ramener `numReplicas` à 1 |
