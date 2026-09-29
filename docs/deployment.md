# Déploiement et exploitation

## État livré

Compose exécute deux services depuis la même image : `web` pour les pages et uploads, `worker` pour la validation complète des photos, les EXIF et la génération des dérivés. Le traitement utilise Pillow et une table Django de jobs ; aucun Redis, Celery ou moteur externe n’est nécessaire. Voir le README pour le périmètre produit livré, distinct des évolutions du document d’architecture.

L’installation cible Linux ARM64 (Raspberry Pi 5) et x86-64. Docker Compose v2.24.4 ou plus récent est recommandé, notamment pour la suppression explicite des ports dans le Compose d’intégration. Python 3 est utilisé une seule fois sur l’hôte pour créer la configuration ; aucune bibliothèque Python tierce n’est installée sur l’hôte.

## Dossiers privés et première installation

Choisir un dossier **hors de toute racine web**. Le code peut se trouver dans un checkout, mais les données et secrets doivent rester à part. Exemple homelab prévu : un nouveau dossier sous `/mnt/symlinks/nvme4To/`, jamais sous le dossier `www` actuel. Ces chemins ne sont pas créés par la simple présence du dépôt.

```bash
python3 scripts/init_env.py \
  --private-root /mnt/symlinks/nvme4To/ma-pixelotheque \
  --allowed-hosts 192.168.0.100,localhost,127.0.0.1 \
  --base-path /ma-pixelotheque
```

Adapter les droits du dossier parent si nécessaire. Le script crée les dossiers en mode `700`, le fichier `app.env` en `600` et conserve l’UID/GID de l’utilisateur qui l’exécute. Exécuté explicitement par root, il cible par défaut `1000:1000` ; `--uid` et `--gid` permettent de choisir le propriétaire des volumes. Il refuse d’écraser un fichier existant et ne corrige pas silencieusement les droits d’un dossier déjà présent.

Le script refuse le dépôt, `/var/www`, `/srv/www` et les chemins comportant `www`, `public_html` ou `htdocs`. Pour une racine personnalisée, ajouter `--document-root /racine/web`. Cette vérification ne peut pas découvrir tous les alias de votre serveur : vérifier le chemin résolu et sa configuration HTTP. L’option `--env-file /chemin/prive/app.env` permet de séparer le secret des volumes, par exemple dans un dossier privé sous `/etc`.

La structure préparée est :

```text
ma-pixelotheque/
├── app.env
├── data/                 # db.sqlite3 et ses journaux
├── media/
│   ├── originals/        # sources intactes ; montage worker en lecture seule
│   ├── thumbnails/       # petites images WebP
│   ├── previews/         # aperçus WebP, sans EXIF/GPS intégrés
│   └── temp/             # réception multipart sur SSD privé
└── backups/
```

Les mounts refusent de créer automatiquement des chemins manquants. Cela évite qu’une faute de frappe produise un dossier vide appartenant à root. Les dossiers doivent être lisibles et inscriptibles par l’UID/GID configuré ; les volumes se trouvent sur un disque local, pas NFS/SMB.

Toutes les commandes `sh scripts/compose.sh "$PIXEL_ENV" …` sont un raccourci pour `docker compose --env-file /chemin/prive/app.env -f docker-compose.yml …`. Le fichier n’est jamais exécuté comme un script shell. Préférer `config --quiet` pour vérifier la syntaxe : `config` sans cette option peut afficher le secret interpolé.

Pour un accès autonome à la racine, appliquer le [démarrage du README](../README.md). Pour le serveur Apache existant et `/ma-pixelotheque/`, utiliser le [Compose autonome de proxy documenté](reverse-proxy/README.md) pour chaque commande. Il nomme le service HTTP `pixelotheque-web` pour éviter l’alias `web` déjà employé sur le réseau partagé.

**Pour cette intégration proxy**, remplacer dans toutes les commandes de ce document `scripts/compose.sh` par `scripts/compose-proxy.sh`, et le service `web` par `pixelotheque-web`. Ne pas combiner les fichiers Compose avec plusieurs `-f` : le wrapper proxy sélectionne uniquement son fichier autonome, lequel hérite de la configuration commune via `extends`. Les commandes sans service, comme `stop` ou `up -d`, visent les deux services de cette installation.

## Variables

| Variable | Valeur du modèle | Rôle |
|---|---|---|
| `DJANGO_SECRET_KEY` | Vide, obligatoire | Secret aléatoire généré par le script ; jamais dans Git ou les logs. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Hôtes sans schéma ni port, séparés par des virgules. Ajouter l’IP/domaine public exact. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Vide | Origines supplémentaires explicitement approuvées, avec schéma et port éventuel, sans chemin. Inutile pour un proxy qui préserve correctement l’origine. |
| `DJANGO_DEBUG` | `False` | Garder désactivé sur une instance utilisée. |
| `APP_BASE_PATH` | Vide | `/ma-pixelotheque` pour un sous-chemin, sans slash final ; le proxy retire ce préfixe. |
| `APP_HTTPS` | `False` | Activer une fois le frontal HTTPS opérationnel ; protège notamment les cookies. |
| `TRUST_PROXY_HTTPS` | `False` | Autorise `X-Forwarded-Proto` uniquement derrière un frontal contrôlé qui le remplace. |
| `TZ` | `Europe/Paris` | Fuseau de l’instance. |
| `APP_UID`, `APP_GID` | `1000` | Identité non root de l’image et des volumes. Changement = rebuild + droits hôte adaptés. |
| `DATA_DIR` | `./var/data` | Chemin du dossier hôte monté sur `/data` ; le script génère un chemin absolu privé. |
| `MEDIA_DIR` | `./var/media` | Chemin du dossier hôte monté sur `/media`, avec sous-dossier `originals` existant. |
| `BIND_ADDRESS` | `127.0.0.1` | Interface du port hôte en mode autonome ; ne pas publier inutilement Gunicorn. |
| `WEB_PORT` | `8000` | Port hôte, ignoré avec le Compose proxy sans publication de port. |
| `PIXEL_IMAGE` | `ma-pixelotheque:local` | Nom/tag local de l’image construite. |

Dans Compose, les chemins **internes** sont fixés : `DATABASE_PATH=/data/db.sqlite3`, `MEDIA_ROOT=/media`, `STATIC_ROOT=/app/staticfiles`. Pour changer l’emplacement sur le serveur, utiliser `DATA_DIR`/`MEDIA_DIR`, pas les chemins internes. Hors Compose, les trois variables internes restent configurables par Django. `DATABASE_TEST_PATH` ne sert qu’aux tests ; ne jamais le pointer sur une base utilisée.

Les valeurs booléennes documentées sont `True`/`False`. Les statiques sont collectés lors du build et l’image est en lecture seule au démarrage : un changement de CSS/JS implique un rebuild de l’image, sans npm. Le secret factice employé par `collectstatic` pendant le build n’est pas la clé utilisée à l’exécution.

`MAP_TILE_URL` permet de remplacer les tuiles OpenStreetMap par un fournisseur compatible avec les variables `{z}`, `{x}` et `{y}`. La valeur vide désactive les tuiles externes tout en conservant les groupes de photos. Le fournisseur reçoit l’adresse IP du navigateur et les zones consultées ; aucune photo ne lui est transmise. Adapter également l’attribution dans `pixelotheque/settings.py` si vous changez de fournisseur, et respecter ses conditions d’utilisation. La valeur par défaut est `https://tile.openstreetmap.org/{z}/{x}/{y}.png`.

## SQLite

Le journal SQLite standard (`DELETE` sur une nouvelle base) est conservé. L’hôte inspecté utilise SQLite 3.40.1 ; l’application Docker utilise sa propre bibliothèque, dont la version doit être contrôlée séparément :

```bash
sh scripts/compose.sh "$PIXEL_ENV" run --rm web \
  python -c 'import sqlite3; print(sqlite3.sqlite_version)'
```

Ne pas activer WAL avant d’avoir vérifié le correctif du défaut « WAL-reset » dans la bibliothèque réellement chargée : version 3.51.3 ou ultérieure, ou branche corrigée comme 3.50.7/3.44.6, ou rétroportage confirmé par le fournisseur. La configuration actuelle ne fournit aucun interrupteur WAL. Les transactions restent courtes ; le passage futur à PostgreSQL sera accompagné de migrations de données et de tests, pas seulement d’un changement de variable. [Avis SQLite](https://www.sqlite.org/wal.html#walreset)

## Photos et worker

Les comptes autorisés envoient des photos JPEG, PNG et WebP fixes dans un album existant ou un nouvel album. Le formulaire classique accepte jusqu’à 20 fichiers ; l’amélioration JavaScript transmet un fichier par requête. Les octets sont comptés pendant la réception multipart puis pendant la copie. Les fichiers qui dépassent la limite sont interrompus sans produire de référence en base. Les fichiers temporaires volumineux vont dans `MEDIA_ROOT/temp` sur SSD, pas dans le petit `/tmp` du conteneur.

L’admission vérifie extension, format et dimensions. Pour WebP, la lecture bornée de l’en-tête évite d’initialiser un décodeur natif dans le serveur web. Chaque source admise reçoit un nom UUID et une empreinte SHA-256 ; elle reste illisible dans l’application jusqu’au décodage complet réussi par le worker. Modifier les métadonnées ou régénérer un aperçu ne modifie jamais les octets de l’original.

Les réglages se trouvent dans Django Admin, « Réglages de l’instance » :

| Réglage | Défaut | Limites admin |
|---|---|---|
| Taille d’un upload | 30 Mio | 1 à 100 Mio |
| Nombre de pixels | 40 millions | 1 à 80 millions |
| Grande dimension d’une miniature | 480 px | 120 à 1 024 px |
| Grande dimension d’un aperçu | 2 048 px | 480 à 4 096 px |
| Qualité WebP | 82 | 40 à 95 |
| Génération automatique | Activée | L’inspection reste obligatoire même si elle est désactivée. |
| Pause du worker | Désactivée | Suspend les nouveaux traitements, sans annuler un enfant déjà lancé. |

Adapter aussi la taille maximale autorisée par le reverse proxy au plafond choisi, avec une marge pour l’enveloppe multipart. Les plafonds de sécurité du code restent prioritaires sur la valeur stockée : 100 Mio et 80 millions de pixels au maximum. Les limites d’admission ne garantissent pas qu’une photo passe la limite mémoire de traitement ; un refus apparaît dans les jobs sans bloquer les photos suivantes.

Le parallélisme est **fixé à un traitement image**. Un verrou d’instance dans `/data/processing.lock` empêche deux superviseurs de traiter simultanément le même stockage local. Chaque réservation utilise un jeton et un bail de 60 secondes, renouvelé pendant le travail. Un job interrompu est repris après expiration ; les tentatives obsolètes ne peuvent pas publier leur résultat. Un job compte au maximum trois tentatives en cas d’erreurs transitoires, avec attente croissante. Une image invalide ou trop coûteuse produit une erreur explicite.

Chaque image est traitée dans un processus enfant jetable : délai maximal de 120 secondes, limite d’espace mémoire virtuel `RLIMIT_AS` de 768 Mio et limite CPU. Cette protection Linux fonctionne indépendamment des limites mémoire Docker absentes sur le homelab actuel. Les réglages Django `PROCESSING_JOB_TIMEOUT` et `PROCESSING_MEMORY_MB` peuvent adapter ces valeurs ; ils ne sont pas exposés comme variables d’environnement par cette livraison. Le code les borne respectivement à 5–600 secondes et 128–1 536 Mio. Le worker garde une priorité CPU réduite et ne propose pas de mode parallèle.

L’administration des traitements affiche attente, exécution, fin, erreur et génération désactivée. L’action de relance ne remplace pas un job actif. Les actions des listes Photos et Albums permettent de demander une régénération ; sélectionner tous les résultats permet une régénération globale. La mise en file se fait par lots de 100 objets pour borner la mémoire. Pour plusieurs milliers de photos, cette opération reste une série d’écritures SQL et peut prendre du temps ; le décodage reste exécuté ensuite par le worker.

Les dérivés sont écrits sous des chemins propres à chaque tentative, puis leurs références sont publiées après contrôle du jeton. Les anciens dérivés remplacés et les fichiers abandonnés par un arrêt brutal ne sont pas purgés automatiquement dans cette version ; surveiller l’espace après des régénérations massives. Ne pas supprimer des fichiers à partir de leur seule ancienneté sans vérifier leurs références.

```bash
sh scripts/compose.sh "$PIXEL_ENV" logs --tail=100 worker
sh scripts/compose.sh "$PIXEL_ENV" stop worker
sh scripts/compose.sh "$PIXEL_ENV" run --rm --no-deps worker python manage.py process_jobs --once
sh scripts/compose.sh "$PIXEL_ENV" up -d worker
```

`--once` traite au maximum **un job** : une photo normale demande d’abord `inspect`, puis `render`. Le worker permanent les enchaîne. Une seconde commande est refusée si un superviseur détient déjà le verrou. Ne pas supprimer le fichier de verrou pendant le fonctionnement ; sa présence seule ne signifie pas que le verrou système est encore détenu.

## Mises à jour

Lire les notes de version, conserver la référence Git/image précédente et réaliser une [sauvegarde](backup-restore.md) avant migration. Exemple depuis le code déjà mis à jour :

```bash
sh scripts/compose.sh "$PIXEL_ENV" build --pull
sh scripts/compose.sh "$PIXEL_ENV" stop
sh scripts/compose.sh "$PIXEL_ENV" run --rm web python manage.py migrate --noinput
sh scripts/compose.sh "$PIXEL_ENV" up -d
sh scripts/compose.sh "$PIXEL_ENV" ps
```

Le build ne modifie pas la base. Arrêter `web` **et** `worker` avant migration ; `stop` sans nom de service le fait pour tout le projet. Les migrations sont une action explicite, jamais exécutées simultanément au démarrage des services. Si une migration échoue, conserver les services arrêtés et examiner l’erreur. Revenir à une ancienne image ne revient pas automatiquement à un ancien schéma : utiliser une sauvegarde compatible lorsque nécessaire.

Les sous-albums nécessitent la migration `library.0003_album_parent`. Elle conserve les albums, photos, membres et partages existants ; tous les albums existants restent au premier niveau jusqu’à leur rangement depuis l’interface. Reconstruire l’image et appliquer la migration avant de redémarrer les services avec ce code.

Les vignettes personnalisées nécessitent ensuite `library.0004_album_cover_photo`. Les albums existants démarrent en mode automatique ; les albums parents peuvent alors afficher une photo de leurs sous-albums accessibles. Cette migration conserve le classement, les photos et leurs droits.

La commande `manage.py createsuperuser` crée le premier administrateur ; les autres comptes se gèrent dans Django Admin. Pour réinitialiser un mot de passe sans le placer dans l’historique :

```bash
sh scripts/compose.sh "$PIXEL_ENV" run --rm web python manage.py changepassword nom_du_compte
```

## Contrôles après démarrage

Vérifier `ps`, les logs limités et `/healthz`, puis une connexion, la création d’un album, son accès avec un second compte et les styles de l’administration. Ajouter une photo de test : elle doit passer de l’attente à un aperçu sans intervention manuelle, avec deux jobs terminés. Sur un sous-chemin, vérifier les redirections et les liens CSS/JS. Les contrôles Docker internes appellent `/healthz` sans préfixe ; depuis le navigateur, l’adresse est `/ma-pixelotheque/healthz`.

En HTTPS, exécuter `manage.py check --deploy` avec les paramètres réels et examiner les avertissements. HSTS se décide au frontal après vérification du domaine et de ses sous-domaines. Le mode HTTP local produit nécessairement des avertissements HTTPS ; il ne convient pas à une exposition Internet.

Compose demande une limite de 512 Mio et d’un cœur CPU pour `web` (un processus Gunicorn, deux threads), et 896 Mio / 0,75 cœur pour le superviseur `worker` et son enfant. Le worker ne publie aucun port. La consommation réelle doit être mesurée sur les photos utilisées.

**Constat sur le homelab actuel :** Docker signale `MemoryLimit=false` et `SwapLimit=false`. La limite mémoire déclarée n’y est donc pas appliquée. L’effectivité des limites doit être contrôlée sur chaque hôte ; l’image seule ne peut pas activer une fonctionnalité manquante du noyau. Aucune modification de démarrage ou du système hôte n’est effectuée par le projet.

## Provenance des versions

Vérifications du 28 septembre 2026 : [Django 5.2 LTS](https://www.djangoproject.com/download/), [image officielle Python](https://hub.docker.com/_/python), [Gunicorn](https://pypi.org/project/gunicorn/), [WhiteNoise](https://pypi.org/project/whitenoise/), [Pillow](https://pypi.org/project/pillow/). Les versions exactes figurent dans `Dockerfile` et `requirements.txt`. Le tag Python fixe la version et la distribution, sans inventer de digest ; une future publication pourra enregistrer le digest effectivement construit.
