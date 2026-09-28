# Ma Pixelothèque

Une photothèque familiale auto-hébergée, pensée pour un Raspberry Pi 5 de 4 Go : Django, pages HTML, CSS et JavaScript légers, SQLite et Docker Compose. Aucun npm ni build frontend.

[English quickstart](README.en.md)

**État du projet : première version du MVP photo.** Le premier objectif est une collection d’environ 5 000 photos, avec une architecture prévue pour grandir progressivement. Les tests utilisent notamment 5 000 entrées synthétiques en base ; cela ne constitue pas un benchmark d’une collection de fichiers réels. Voir le [bilan de validation](docs/validation.md).

## Fonctionnalités

- Upload multiple JPEG, PNG et WebP, vers un album existant ou créé à cette occasion. Originaux conservés intacts, extraction EXIF et miniatures en arrière-plan.
- Albums familiaux, privés ou restreints, classement d’une photo dans plusieurs albums ; comptes famille, invité et administrateur, sans inscription publique.
- Timeline par année, mois et jour, pagination, chargement différé et sélection stable dans les périodes denses.
- Carte Leaflet avec groupes géographiques, recherche par texte et filtres, favoris personnels, détail photo avec navigation et modification des informations.
- Liens de partage aléatoires limités à un album, désactivables, révocables et avec expiration optionnelle. Les familles peuvent partager leurs propres albums selon le réglage admin.
- Administration des traitements, relance et régénération des aperçus, réglages de qualité et de limites. Thèmes clair, sombre et système.

Les permissions sont appliquées avant pagination, recherche et agrégation géographique, et à chaque téléchargement. Les aperçus n’incluent pas les métadonnées EXIF/GPS ; un téléchargement d’original conserve toutes les données du fichier source.

## Démarrage avec Docker

Prérequis : Linux ARM64 ou x86-64, Docker avec Compose v2 récent et Python 3 pour préparer la configuration. Les commandes suivantes construisent l’image, téléchargent les dépendances dans cette image et créent une nouvelle installation ; elles ne doivent être exécutées qu’après vérification des chemins.

Cloner le projet hors de toute racine publique du serveur web, puis préparer une installation :

```bash
git clone https://github.com/Macsim51/ma-pixelotheque.git
cd ma-pixelotheque
python3 scripts/init_env.py --private-root "$HOME/.local/share/ma-pixelotheque"
export PIXEL_ENV="$HOME/.local/share/ma-pixelotheque/app.env"
sh scripts/compose.sh "$PIXEL_ENV" config --quiet
sh scripts/compose.sh "$PIXEL_ENV" build --pull
sh scripts/compose.sh "$PIXEL_ENV" run --rm web python manage.py migrate --noinput
sh scripts/compose.sh "$PIXEL_ENV" run --rm web python manage.py createsuperuser
sh scripts/compose.sh "$PIXEL_ENV" up -d
sh scripts/compose.sh "$PIXEL_ENV" ps
```

Ouvrir [http://127.0.0.1:8000/](http://127.0.0.1:8000/) sur la machine hôte. L’administration est disponible sur `/admin/`. Sur un serveur distant, utiliser un tunnel SSH ou configurer un reverse proxy ; le port n’est volontairement lié qu’à l’interface loopback. Les comptes famille et invité sont créés par l’administrateur, sans inscription publique.

Le script prépare un secret aléatoire non affiché, un fichier `app.env` privé et les dossiers persistants. Il ne démarre aucun service et refuse de remplacer une configuration existante. Ne jamais créer de secret, base ou média dans un dossier servi par Apache/Nginx. `.gitignore` n’est pas un contrôle d’accès HTTP.

Pour le serveur homelab et le préfixe `/ma-pixelotheque/`, suivre [l’intégration Apache](docs/reverse-proxy/README.md). Aucune configuration Apache existante n’est modifiée par ces fichiers.

## Ce que lance Compose

Un service `web` non root : Django 5.2 LTS, Gunicorn avec un processus et deux threads, WhiteNoise pour les fichiers d’interface. Un service `worker` traite une photo à la fois avec Pillow, dans un processus enfant avec limites de mémoire et de durée. La file de traitements se trouve dans SQLite, sans Redis ni Celery.

La base et les médias se trouvent dans des volumes hôte ; les statiques sont collectés à la construction de l’image. Le worker monte les originaux en lecture seule. `/healthz` contrôle le service et sa base sans révéler de configuration. SQLite utilise son journal standard ; PostgreSQL reste une évolution future.

Les limites Docker doivent être supportées par le noyau hôte. Voir les [limites de ressources](docs/deployment.md), notamment pour Raspberry Pi. Les bibliothèques HTMX et Leaflet sont incluses localement avec leurs licences ; seule la carte charge par défaut des tuiles OpenStreetMap externes. Définir `MAP_TILE_URL=` dans le fichier privé de configuration désactive le fond externe tout en gardant les groupes de photos.

## Première utilisation

Créer les comptes dans l’administration avancée, choisir le rôle **Famille**, puis se connecter avec l’un de ces comptes. Créer un album ou utiliser **Ajouter des photos**. Les photos reçues apparaissent en attente pendant que le worker extrait les informations et prépare les aperçus. Les invités doivent être ajoutés explicitement aux albums autorisés.

Le propriétaire d’un album ouvre **Partager** pour créer un lien. Son adresse n’est affichée qu’une fois : seule son empreinte est conservée. Désactiver le partage familial dans les réglages retire la gestion des liens aux familles, sans révoquer les liens existants ; ceux-ci restent gérables par l’admin.

Les vidéos, le scan de dossiers, la reconnaissance faciale et les applications mobiles natives sont hors périmètre. La suppression définitive des photos n’est pas encore proposée ; la suppression d’un album qui laisserait une photo sans album est bloquée.

## Exploitation et développement

- [Installation, variables et mises à jour](docs/deployment.md)
- [Apache, Nginx, Caddy et Traefik](docs/reverse-proxy/README.md)
- [Sauvegarde et restauration](docs/backup-restore.md)
- [Contribuer et exécuter les tests](CONTRIBUTING.md)
- [Signaler un problème de sécurité](SECURITY.md)

```bash
sh scripts/compose.sh "$PIXEL_ENV" logs --tail=100 web worker
sh scripts/compose.sh "$PIXEL_ENV" exec web python manage.py check
sh scripts/compose.sh "$PIXEL_ENV" stop
```

Projet open source sous [licence MIT](LICENSE). Projet d’origine : [Ma Pixelothèque par Macsim51](https://github.com/Macsim51/ma-pixelotheque). Conserver la notice de copyright et le texte de licence lors des redistributions. Les bibliothèques tierces conservent leurs propres licences.
