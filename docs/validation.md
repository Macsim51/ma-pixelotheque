# Validation de la première version

Ce document distingue les vérifications reproductibles des mesures restant à
faire sur une collection réelle. Les commandes de test se trouvent dans
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Environnement et isolation

Les vérifications initiales utilisent l’image ARM64 Python 3.13.15, Django 5.2.17
et Pillow 12.3.0, construite sur le serveur homelab. Les conteneurs de test sont
temporaires, sans réseau extérieur ni port publié. Ils utilisent des bases SQLite
et des photos synthétiques ; aucune donnée familiale n’est requise.

L’image fonctionne avec un utilisateur non root et un système de fichiers en
lecture seule, à l’exception des volumes de données et des dossiers temporaires.
Les dépendances de l’application sont installées dans Docker. Aucun paquet
Python ou JavaScript n’est installé sur l’hôte par ces tests.

## Vérifications automatisées

La suite initiale complète passe **202 tests** dans Docker. Le parcours HTTP réel
passe séparément. Les deux configurations Compose (autonome et derrière Apache)
ont été validées ; le Compose proxy autonome ne publie aucun port applicatif.

`check --deploy` en mode HTTPS ne signale que `security.W004` : HSTS reste à
configurer au niveau du domaine lorsqu’il sera entièrement publié en HTTPS.
Le MVP peut vivre sous un sous-chemin d’un frontal partagé ; il n’impose donc pas
ce réglage à tous les autres services du domaine.

- Comptes, limitations de connexion, CSRF, rôles, albums privés/restreints et
  invitations ; refus des accès directs aux médias non autorisés.
- Recherche, favoris, périodes, navigation entre photos et frontières de dates.
  Une base de 5 000 entrées synthétiques vérifie la pagination et les agrégats
  de carte ; les sélections représentatives restent stables.
- Réception des images, formats/extensions, limites de taille/pixels, images
  animées et corrompues, intégrité des originaux, EXIF et orientation. Les tests
  font réellement travailler les processus enfants Pillow.
- Reprise des jobs, expiration des réservations, tentatives bornées, protection
  contre la publication d’un ancien traitement et conservation des permissions.
- Partages limités à un album, tokens stockés sous forme d’empreinte, expiration,
  révocation, accès aux fichiers et absence d’union avec la session connectée.
- Initialisation sûre des chemins privés, migrations sans dérive et collecte
  des statiques avec le manifeste de production.

`tests/smoke_http.py` démarre Gunicorn et un proxy local temporaire qui retire le
préfixe `/ma-pixelotheque`. Il vérifie le login, les cookies et la protection CSRF,
les statiques, l’upload multipart d’un PNG, les deux traitements du worker,
l’original identique octet pour octet, la miniature WebP, les favoris et un partage
anonyme effectivement révoqué. Un second démarrage en mode HTTPS vérifie la
redirection des pages et le contrôle de santé interne.

Le script de sauvegarde a également été vérifié avec une base synthétique et des
fichiers témoins : copie cohérente, restauration indépendante et refus d’écraser
une destination existante ou de suivre des liens symboliques dans les médias.

## Vérification visuelle et limites

Des pages rendues par les vrais templates ont été ouvertes dans Chromium déjà
présent sur le serveur, en largeur bureau et mobile, avec les thèmes clair et
sombre. Les illustrations utilisées pour cette vérification sont fictives. Cette
vérification de mise en page ne constitue pas un test de chaque interaction sur
tous les navigateurs.

La base de 5 000 entrées ne mesure ni le débit d’import de 5 000 originaux, ni la
mémoire maximale sur de grandes images, ni la latence sous charge concurrente.
Ces mesures doivent utiliser la collection réelle et les services voisins du
Raspberry Pi. Les plafonds du worker réduisent l’impact des images coûteuses,
mais peuvent aussi conduire au refus d’une image admise par sa taille en pixels.

Sur le homelab inspecté, le noyau n’applique pas les plafonds mémoire Docker.
La limite d’espace mémoire virtuel de l’enfant reste active ; elle ne remplace
pas un contrôle mémoire de tous les processus du conteneur. Voir
[les consignes d’exploitation](deployment.md).

## Mise en service LAN du 29 septembre 2026

Après autorisation, l’application a été raccordée à l’Apache existant. La
configuration et l’image précédentes ont été sauvegardées. Une image dérivée
ajoute les modules de proxy ; les fichiers de retour arrière conservent le refus
d’accès aux sources dans le documentroot.

Le déploiement utilise le Compose proxy autonome et le service
`pixelotheque-web`, pour éviter l’alias automatique `web` déjà utilisé sur le
réseau partagé. Aucun port hôte n’est publié par la photothèque. Les données sont
sur le NVMe privé et les originaux sont montés en lecture seule dans le worker.

Les contrôles réels sur l’URL LAN passent : santé, connexion administrateur,
CSRF, cookies préfixés, fichiers d’interface, huit pages authentifiées et refus
des chemins source. SQLite signale une intégrité correcte et utilise le journal
standard. Un chemin de partage de contrôle n’apparaît pas dans les logs Apache
et est expurgé dans les logs Django.

Seul le conteneur Apache a été recréé. Les autres conteneurs existants ont conservé
leur identifiant et leur état actif. Six pages témoins, dont trois sites PHP,
conservent leurs codes HTTP et leurs redirections. Les étapes HTTPS et sauvegarde
sur un autre support restent à réaliser avant une exposition Internet.
