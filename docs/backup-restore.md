# Sauvegarde et restauration

Les comptes, albums, permissions, métadonnées, liens de partage et jobs résident dans SQLite. Les photos résident dans les volumes médias : base et fichiers doivent être sauvegardés ensemble. Les originaux sont irremplaçables ; les miniatures et aperçus peuvent être régénérés, mais cette procédure copie tous les médias pour une restauration immédiate.

## Sauvegarder une instance

Cette première procédure assume une courte indisponibilité. Elle arrête tous les écrivains, produit une copie SQLite par son API de sauvegarde puis copie les médias. Une copie du seul fichier `.sqlite3` pendant son utilisation n’est pas une méthode de sauvegarde ; avec WAL, des transactions peuvent encore résider dans le journal. [API SQLite](https://www.sqlite.org/backup.html)

Depuis le dépôt, définir vos chemins privés et une destination neuve. Les exemples utilisent les volumes créés par `init_env.py` :

```bash
export PIXEL_ENV=/chemin/prive/ma-pixelotheque/app.env
export PIXEL_BACKUPS=/chemin/prive/ma-pixelotheque/backups
export PIXEL_SNAPSHOT=avant-mise-a-jour-2026-09-28

sh scripts/compose.sh "$PIXEL_ENV" stop
sh scripts/compose.sh "$PIXEL_ENV" run --rm --no-deps \
  -v "$PIXEL_BACKUPS:/backup" \
  web python scripts/backup.py \
  "/backup/$PIXEL_SNAPSHOT" --services-stopped --label reference-git-ou-image
sh scripts/compose.sh "$PIXEL_ENV" up -d
```

Avec l’intégration Apache, remplacer `scripts/compose.sh` par `scripts/compose-proxy.sh` dans **chaque** commande et le nom de service `web` par `pixelotheque-web`. Le fichier proxy est autonome : ne pas le superposer au Compose principal, ce qui réintroduirait l’alias réseau générique `web`. `stop` vise tous les services du projet : le serveur web **et** `worker` doivent être arrêtés. Vérifier qu’aucune commande ponctuelle `process_jobs` ou autre maintenance indépendante ne modifie les données.

Le script ne sait pas arrêter les services à votre place. `--services-stopped` confirme que cette condition a été vérifiée. Il refuse une destination existante et les chemins contenus dans les volumes source. Il utilise une connexion source en lecture seule et livre un fichier SQLite autonome en journal standard, avec contrôle `quick_check`. Les médias ne doivent pas contenir de liens symboliques.

Une sauvegarde terminée contient :

```text
avant-mise-a-jour-2026-09-28/
├── db.sqlite3
├── media/
└── manifest.json
```

`manifest.json` est écrit en dernier. S’il manque ou si la commande échoue, conserver la tentative pour diagnostic et ne pas la restaurer. Le script ne supprime jamais une ancienne sauvegarde. Il ne remplace pas une vérification périodique de restauration complète.

Le fichier `app.env` n’est pas inclus. En conserver une copie séparée protégée, avec la référence du code et de l’image correspondants. Ne pas le copier dans le dépôt ni joindre une sauvegarde à une issue publique. Après sauvegarde, transférer une copie sur un autre support privé : un dossier `backups` sur le même disque ne protège pas d’une panne de ce disque. Avec beaucoup de médias, l’interruption couvre la durée de copie ; une sauvegarde par instantané cohérent pourra être ajoutée ultérieurement.

## Restaurer sans écraser les données actuelles

Utiliser d’abord la version du code/image indiquée par la sauvegarde. Préparer de nouveaux dossiers privés permet de garder la base actuelle et ses journaux ensemble, sans risque de mélanger un ancien fichier `-wal` avec une nouvelle base.

```bash
export PIXEL_SOURCE=/chemin/prive/ma-pixelotheque/backups/avant-mise-a-jour-2026-09-28
export PIXEL_RESTORE=/chemin/prive/ma-pixelotheque-restauree

sh scripts/compose.sh "$PIXEL_ENV" stop
umask 077
mkdir "$PIXEL_RESTORE"
mkdir "$PIXEL_RESTORE/data"
cp -p "$PIXEL_SOURCE/db.sqlite3" "$PIXEL_RESTORE/data/db.sqlite3"
cp -a "$PIXEL_SOURCE/media" "$PIXEL_RESTORE/media"
cp -p "$PIXEL_ENV" "$PIXEL_RESTORE/app.env"
```

Vérifier la présence du manifeste, puis éditer la nouvelle copie de `app.env` : `DATA_DIR` doit viser le nouveau dossier `data`, et `MEDIA_DIR` le nouveau dossier `media`. Restaurer aussi la configuration secrète correspondante si le secret ou les autres paramètres ont changé depuis la sauvegarde. Vérifier les propriétaires et les modes `700`/`600` ; ils doivent correspondre à `APP_UID`/`APP_GID`.

```bash
export PIXEL_ENV="$PIXEL_RESTORE/app.env"
sh scripts/compose.sh "$PIXEL_ENV" config --quiet
sh scripts/compose.sh "$PIXEL_ENV" run --rm --no-deps web \
  python -c 'import sqlite3; c=sqlite3.connect("file:/data/db.sqlite3?mode=ro", uri=True); result=c.execute("PRAGMA integrity_check").fetchall(); c.close(); print(result); raise SystemExit(0 if result == [("ok",)] else 1)'
sh scripts/compose.sh "$PIXEL_ENV" run --rm --no-deps web python manage.py showmigrations
sh scripts/compose.sh "$PIXEL_ENV" up -d
sh scripts/compose.sh "$PIXEL_ENV" ps
```

Ne pas redémarrer si le contrôle d’intégrité échoue. Avec exactement la version de code sauvegardée, les migrations doivent être déjà appliquées. Pour reprendre ensuite une mise à jour, suivre sa procédure et sauvegarder à nouveau avant les migrations.

Vérifier connexion, accès invité, visibilités privée/restreinte, validité des liens privés et quelques originaux/dérivés. Le worker reprend les jobs restés en attente ; un job sauvegardé « en cours » redevient disponible à l’expiration de son bail. Un arrêt propre relâche normalement le job actif avant la copie.

Conserver les anciens volumes jusqu’à validation. Lors d’un exercice de restauration sur la même machine, utiliser un nom de projet et un port distincts afin de ne pas remplacer l’instance en service. Le parcours synthétique de [CONTRIBUTING.md](../CONTRIBUTING.md) fournit aussi un contrôle HTTP + pipeline sur des données jetables.
