# Reverse proxy

Les exemples publient l’application sous `/ma-pixelotheque/`. Le proxy **retire** ce préfixe avant d’appeler Gunicorn, préserve l’en-tête `Host` et remplace `X-Forwarded-Proto` par le protocole connu du frontal. Django utilise `APP_BASE_PATH=/ma-pixelotheque` pour ses liens, cookies, redirections et statiques. Ne pas ajouter le préfixe une deuxième fois dans l’URL du backend.

Tous les chemins de l’application, y compris `/static/` et `/healthz`, passent par le proxy. WhiteNoise sert les statiques intégrés à l’image ; il n’existe aucun alias HTTP vers le stockage privé. Les dossiers du code, `.env`, base et sauvegardes ne doivent pas être accessibles comme fichiers par le serveur web.

Les données du homelab sont prévues sous `/mnt/symlinks/nvme4To/ma-pixelotheque/`, hors de `www`. Les UID/GID des services applicatifs doivent pouvoir écrire les répertoires de base, médias et temporaires ; le fichier d’environnement doit rester lisible uniquement par son compte d’exploitation, par exemple avec le mode `0600`. Utiliser des répertoires privés `0700` ou `0750` selon le groupe choisi, sans accès global en écriture. Apache n’a pas besoin de lire la base, les médias, les secrets ou leurs sauvegardes : lui donner seulement l’accès réseau au backend. Ces permissions Unix complètent les contrôles HTTP, elles ne les remplacent pas.

## Apache du homelab

Contexte relevé : Apache est dans le conteneur `monsite`, publié sur le port hôte `8888`, réseau Docker `my_network`. L’alias générique `web` existe déjà sur ce réseau. Le Compose autonome fourni nomme donc son service **`pixelotheque-web`** et ne publie aucun port. Un simple alias supplémentaire ne suffit pas : Compose conserve aussi le nom du service dans son DNS, ce qui provoquerait une collision avec un second service nommé `web`.

Ce fichier hérite des paramètres du Compose principal avec `extends`, sans démarrer son service `web`. Le worker utilise uniquement le réseau privé du projet ; il n’a pas besoin de joindre Apache. **Ne pas superposer les deux fichiers avec plusieurs options `-f`** et ne pas ajouter `--project-directory` : le wrapper dédié fixe les chemins, et Compose résout les paramètres hérités depuis leur fichier d’origine.

Le fichier privé de configuration doit notamment contenir :

```dotenv
DJANGO_ALLOWED_HOSTS=192.168.0.100,localhost,127.0.0.1
APP_BASE_PATH=/ma-pixelotheque
APP_HTTPS=False
TRUST_PROXY_HTTPS=False
PROXY_NETWORK=my_network
```

Le port `8888` appartient à Apache ; il ne figure pas dans `DJANGO_ALLOWED_HOSTS`. Si une origine CSRF supplémentaire est nécessaire, elle comprend le schéma et le port, par exemple `http://192.168.0.100:8888`, jamais le chemin `/ma-pixelotheque/`.

Depuis le dépôt, après préparation du fichier privé :

```bash
export PIXEL_ENV=/mnt/symlinks/nvme4To/ma-pixelotheque/app.env
pixel_compose() {
  sh scripts/compose-proxy.sh "$PIXEL_ENV" "$@"
}

pixel_compose config --quiet
pixel_compose build --pull
pixel_compose run --rm pixelotheque-web python manage.py migrate --noinput
pixel_compose run --rm pixelotheque-web python manage.py createsuperuser
pixel_compose up -d
pixel_compose ps
```

Ces commandes supposent que le réseau externe existe. Le projet ne crée ni ne modifie ce réseau automatiquement. Toujours utiliser `compose-proxy.sh` pour cette installation, notamment pour les sauvegardes, mises à jour et commandes ponctuelles. Dans les exemples d’exploitation écrits pour l’installation autonome, remplacer `scripts/compose.sh` par `scripts/compose-proxy.sh`, et le nom de service `web` par `pixelotheque-web`. Par exemple :

```bash
pixel_compose logs --tail=100 pixelotheque-web worker
pixel_compose exec pixelotheque-web python manage.py check
pixel_compose exec pixelotheque-web python manage.py changepassword nom_du_compte
```

Le wrapper ne charge jamais `app.env` comme du code shell. Le contexte de build reste la racine du dépôt. Les chemins absolus des volumes générés par `init_env.py` sont conservés. Contrôler après démarrage que seul `pixelotheque-web` a rejoint `my_network`, sans nouvel alias `web`, et que le service ne possède aucun port hôte publié.

Le fragment [apache-http.conf](apache-http.conf) doit être intégré au VirtualHost existant, avant toute règle de proxy plus générale. Les modules `proxy`, `proxy_http`, `headers` et `setenvif` doivent être activés dans l’image/configuration persistante d’Apache. L’inspection initiale n’a pas trouvé les modules proxy chargés : leur activation, la modification de l’image `php-apache-custom` et le rechargement d’Apache sont des opérations séparées à faire valider. Ces fichiers ne les effectuent pas. Intégrer également les conditions de journalisation décrites plus bas avant de créer un lien privé utilisable.

Avant rechargement, contrôler la configuration avec `apachectl -t` dans l’environnement Apache approprié. Vérifier ensuite [l’URL réseau](http://192.168.0.100:8888/ma-pixelotheque/), les formulaires, les redirections, les CSS/JS et `/ma-pixelotheque/healthz`. Le backend n’est pas `localhost` vu depuis `monsite` : il est `pixelotheque-web:8000`.

Si le checkout reste dans le documentroot Apache, adapter le bloc `<Directory>` actif de l’exemple à son chemin réel vu depuis Apache. Il refuse le service direct des sources ; les routes applicatives continuent à passer par `ProxyPass`. Vérifier les éventuels autres alias vers ce dossier. Si le checkout n’est pas monté dans Apache, le bloc peut être omis. Secrets et médias restent dans tous les cas hors de cette arborescence.

## HTTPS et autres reverse proxies

Exemples fournis : [Apache HTTPS](apache-https.conf), [Nginx](nginx.conf), [Caddy](Caddyfile) et [Traefik](traefik.yml). Adapter domaine, certificats et adresse du backend. Un proxy exécuté sur l’hôte peut joindre le port loopback `127.0.0.1:8000`. Un proxy dans Docker doit partager un réseau privé avec le service et utiliser son alias ; il ne doit pas joindre son propre `127.0.0.1`.

Une fois HTTPS opérationnel :

```dotenv
DJANGO_ALLOWED_HOSTS=photos.example.org,localhost,127.0.0.1
APP_BASE_PATH=/ma-pixelotheque
APP_HTTPS=True
TRUST_PROXY_HTTPS=True
```

Ne faire confiance à l’en-tête HTTPS que si tous les accès à Gunicorn viennent d’un proxy contrôlé qui écrase la valeur reçue du client. Une boucle de redirection vers HTTPS indique souvent un en-tête manquant ou une confiance proxy incorrecte. Le contrôle interne `/healthz` est exempté de redirection pour fonctionner dans le conteneur.

À la racine d’un domaine, utiliser `APP_BASE_PATH=` et adapter la route du proxy pour transmettre `/` sans retrait de préfixe. Les paramètres des certificats, DNS et du frontal restent ceux de votre hébergement : aucun reverse proxy supplémentaire n’est imposé par Compose.

## Liens privés et journaux

Un token placé dans `/ma-pixelotheque/s/<token>/` autorise la lecture de l’album. Le masquage des logs Django ne s’étend pas aux proxies. Les pages et fichiers partagés renvoient `Referrer-Policy: no-referrer` et `Cache-Control: private, no-store` ; conserver ces en-têtes et ne pas activer de cache partagé sur ces routes.

### Apache : modifier les journaux existants

Les fragments posent ce marqueur de requête dans le VirtualHost :

```apache
SetEnvIf Request_URI "^/ma-pixelotheque/s/" pixelotheque_private
```

Ce marqueur **ne suffit pas**. Pour chaque directive `CustomLog` active, garder sa destination et son format, puis ajouter la condition d’exclusion. Par exemple, remplacer :

```apache
CustomLog /proc/self/fd/1 combined
```

par :

```apache
CustomLog /proc/self/fd/1 combined env=!pixelotheque_private
```

Il faut remplacer la directive, pas ajouter un deuxième `CustomLog` : Apache peut écrire chaque requête dans plusieurs journaux. Contrôler aussi les fichiers inclus et les `GlobalLog` définis au niveau serveur ; ceux-ci s’appliquent même aux VirtualHosts qui possèdent leurs propres logs. Leur ajouter également une exclusion appropriée. Adapter le préfixe si l’application est servie à la racine. [Journalisation conditionnelle Apache](https://httpd.apache.org/docs/2.4/logs.html#conditional), [CustomLog et GlobalLog](https://httpd.apache.org/docs/2.4/mod/mod_log_config.html).

Si une condition existe déjà, la conserver. Pour un ancien `env=!dontlog`, on peut marquer les partages avec le même indicateur d’exclusion au lieu de remplacer cette condition. Pour un ancien `env=log_this`, conserver l’exigence de cet indicateur et exclure le nouveau :

```apache
CustomLog /proc/self/fd/1 combined "expr=-n reqenv('log_this') && -z reqenv('pixelotheque_private')"
```

Ces expressions sont celles d’Apache 2.4. Valider la configuration finale avec `apachectl -t`, puis vérifier ses journaux avec un lien jetable et des réponses 200/404, sans recopier ce lien dans un ticket ou un journal d’exploitation. [Expressions Apache](https://httpd.apache.org/docs/2.4/expr.html).

### Autres exemples

- [Nginx](nginx.conf) désactive les journaux d’accès dans toute la location applicative avec `access_log off`, y compris ceux hérités. Une configuration personnalisée peut conserver les autres routes, à condition d’exclure toutes les routes de partage dans leur location finale et après d’éventuelles redirections internes. [Module de logs Nginx](https://nginx.org/en/docs/http/ngx_http_log_module.html).
- [Caddy](Caddyfile) utilise `log_skip /ma-pixelotheque/s/*` avant le retrait du préfixe. L’exemple demande Caddy **2.8 ou ultérieur** ; cette directive portait auparavant le nom `skip_log`. Ne pas ajouter un autre logger qui réintroduit les URLs privées. [Directive log_skip](https://caddyserver.com/docs/caddyfile/directives/log_skip).
- [Traefik](traefik.yml) désactive `observability.accessLogs` et `observability.tracing` pour le routeur applicatif. L’exemple cible **Traefik 3.3 ou ultérieur**, dont la documentation décrit ces options. Ne pas supposer sa compatibilité avec Traefik 2 ou des versions antérieures de Traefik 3 ; adapter leur politique de journalisation avant utilisation. [Observability dans Traefik 3.3](https://doc.traefik.io/traefik/v3.3/routing/routers/#observability).

Ces règles concernent les journaux d’accès et les traces désignés. Les journaux d’erreur des proxies peuvent également inclure une URI, notamment lors d’une panne du backend. Configurer leur collecteur pour masquer le segment secret de `/s/<token>/` avant stockage ou diffusion, limiter la lecture des journaux aux comptes d’exploitation, et ne pas activer la capture debug des requêtes réelles. La validation d’un déploiement comprend donc aussi une requête de partage avec backend indisponible. Aucun filtre Django ne peut supprimer une URL déjà enregistrée par Apache, Docker ou un collecteur externe.

## Carte et tuiles externes

La page Carte charge les tuiles du fournisseur choisi par `MAP_TILE_URL`, par défaut OpenStreetMap. Les fichiers photo restent sur l’instance ; le fournisseur reçoit néanmoins l’adresse IP du navigateur et les zones cartographiques consultées. Une instance sans accès externe peut choisir un fournisseur interne ou laisser cette URL vide, au prix de l’absence de fond de carte.

La carte connectée renvoie `Referrer-Policy: strict-origin-when-cross-origin` : le fournisseur reçoit l’origine du site, sans chemin privé. Les pages de partage utilisent `no-referrer` et ne chargent pas de carte externe. Ne pas remplacer ces politiques par une règle de proxy uniforme qui empêcherait le référent de la carte ou exposerait celui d’un lien privé.

Les tuiles publiques OpenStreetMap exigent notamment un référent valide depuis une page web, une attribution visible et le respect du cache HTTP des tuiles. Ne pas leur appliquer le `no-store` des photos, ne pas organiser de téléchargement massif ni de préchargement hors ligne. Une autre offre de tuiles peut avoir ses propres conditions. [Politique officielle des tuiles OpenStreetMap](https://operations.osmfoundation.org/policies/tiles/).

## Limitation des tentatives de connexion

Le même mécanisme protège la connexion familiale et Django Admin. Les échecs sont comptés pendant 15 minutes, avec plafonds par combinaison identifiant/adresse, identifiant et adresse. L’adresse provient exclusivement de `REMOTE_ADDR` ; l’application ne fait pas confiance à `X-Forwarded-For`.

Conséquence actuelle : derrière Apache, tous les navigateurs ont l’adresse du proxy pour le plafond par IP. Les limites par identifiant restent distinctes, mais beaucoup d’échecs cumulés peuvent ralentir tous les utilisateurs passant par ce proxy. Aucun mécanisme de déduction de l’IP réelle n’est annoncé dans ce premier incrément. Les seuils sont définis dans les réglages Django, pas par variables d’environnement pour l’instant.

La commande `python manage.py purge_login_attempts` nettoie les entrées expirées ; `python manage.py clearsessions` nettoie les sessions expirées. Leur planification pourra être faite par l’exploitant, sans ajouter de service permanent.
