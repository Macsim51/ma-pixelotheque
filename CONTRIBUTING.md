# Contribuer

Lire [l’architecture](docs/architecture.md) et garder les changements petits : une règle métier, son comportement observable et les tests nécessaires. La bibliothèque inclut maintenant l’upload et le pipeline d’images réel. Toute évolution doit préserver le contrôle d’accès, les octets des originaux et les bornes de traitement adaptées au Raspberry Pi.

## Environnement

La cible est Python 3.13 sous Linux, avec les dépendances exactes de `requirements.txt`. Les tests utilisent Django et la bibliothèque standard : aucun framework de test supplémentaire. Docker permet de travailler sans installer de paquets Python sur le serveur hôte.

Après préparation de la configuration et construction de l’image selon le README :

```bash
sh scripts/compose.sh "$PIXEL_ENV" run --rm \
  -e DJANGO_SETTINGS_MODULE=pixelotheque.settings_test \
  -e DATABASE_PATH=/tmp/pixelotheque-check.sqlite3 \
  -e DATABASE_TEST_PATH=/tmp/pixelotheque-tests.sqlite3 \
  web python manage.py test --noinput

sh scripts/compose.sh "$PIXEL_ENV" run --rm \
  -e DJANGO_SETTINGS_MODULE=pixelotheque.settings_test \
  -e DATABASE_PATH=/tmp/pixelotheque-check.sqlite3 \
  web python manage.py makemigrations --check --dry-run
```

Ces bases de test restent dans le `/tmp` éphémère du conteneur. Ne jamais lancer les tests en réutilisant le fichier de production comme `DATABASE_TEST_PATH`. `settings_test` utilise un hasher rapide réservé aux tests et ne doit jamais servir l’application.

Le parcours HTTP réel vérifie aussi Gunicorn, les cookies, CSRF, les statiques, le sous-chemin, un upload et les deux jobs `inspect`/`render`, avec une base, des comptes et des images synthétiques jetables. Ses ports restent sur la boucle locale du conteneur et ne sont pas publiés :

```bash
sh scripts/compose.sh "$PIXEL_ENV" run --rm web python tests/smoke_http.py
```

Les tests ciblés du pipeline exécutent de vrais enfants Pillow avec leurs limites et vérifient notamment orientation, EXIF, conservation de l’original, admission, permissions révoquées pendant l’upload, récupération des baux et rejet d’une publication périmée :

```bash
sh scripts/compose.sh "$PIXEL_ENV" run --rm \
  -e DJANGO_SETTINGS_MODULE=pixelotheque.settings_test \
  -e DATABASE_PATH=/tmp/pixelotheque-check.sqlite3 \
  -e DATABASE_TEST_PATH=/tmp/pixelotheque-tests.sqlite3 \
  web python manage.py test tests.test_processing --noinput
```

Les fichiers et verrous de ces tests résident dans des dossiers temporaires propres à chaque test. Le processus enfant n’ouvre jamais la base : il renvoie des métadonnées bornées au superviseur, qui publie uniquement si son jeton et son bail sont toujours valides. Ne pas ajouter de génération d’image aux vues de consultation.

Pour travailler hors Docker, créer un environnement virtuel privé et installer `requirements.txt` uniquement après accord de l’administrateur de la machine. Le projet n’exige ni Node.js ni npm.

## Avant une proposition de changement

- Vérifier permissions, données invalides et accès directs aux URL, y compris les comptes invités.
- Ajouter les migrations correspondant aux changements de modèles ; ne pas modifier une migration déjà publiée.
- Exécuter `manage.py check`, `makemigrations --check --dry-run` et les tests concernés. La CI utilise une base SQLite sur disque et vérifie aussi `collectstatic` avec les réglages de production.
- Actualiser la documentation si une variable, une commande ou une règle change.
- Garder secrets, images personnelles, bases et sauvegardes hors de Git. Utiliser des données de test synthétiques.

Les dépendances et le tag de l’image Python sont modifiés explicitement puis testés. Pas de mise à jour silencieuse de la stack ni d’ajout de service externe sans justification. Les workflows GitHub ne déploient rien et ne publient aucune image.

Le projet est distribué sous [licence MIT](LICENSE). Les contributions proposées
pour intégration doivent pouvoir être distribuées sous cette même licence.
Conserver l’attribution du projet d’origine et les licences des fichiers tiers.
