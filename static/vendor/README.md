# Bibliothèques servies localement

Ces fichiers sont des distributions précompilées officielles, versionnées pour
éviter une dépendance CDN à l’exécution. Aucun npm n’est nécessaire pour installer
ou mettre à jour l’application.

| Bibliothèque | Version | Provenance |
|---|---|---|
| HTMX | 2.0.11 | [Documentation officielle](https://htmx.org/docs/), distribution jsDelivr `htmx.org@2.0.11` |
| Leaflet | 1.9.4 | [Distribution officielle](https://leafletjs.com/download.html), distribution unpkg `leaflet@1.9.4` |

Les empreintes SRI publiées par les projets ont été vérifiées pour le JavaScript
HTMX et les fichiers JavaScript/CSS Leaflet au téléchargement. `SHA256SUMS`
enregistre les empreintes des fichiers stockés ici. Les licences sont conservées
dans chaque sous-dossier ; elles restent celles de leurs auteurs respectifs.

Lors d’une mise à jour, remplacer explicitement les fichiers, vérifier les
empreintes officielles disponibles et les licences, actualiser `SHA256SUMS`,
puis tester les favoris HTMX, la carte et `collectstatic`.
