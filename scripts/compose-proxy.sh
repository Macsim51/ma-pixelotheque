#!/bin/sh
# Intégration au réseau d'un reverse proxy existant, sans alias générique web.
set -eu

if [ "$#" -lt 2 ]; then
    echo "Usage: sh scripts/compose-proxy.sh /chemin/prive/app.env [options Compose] commande" >&2
    exit 2
fi

pixel_env=$1
shift
case "$pixel_env" in
    /*) ;;
    *) echo "Le fichier d'environnement doit avoir un chemin absolu." >&2; exit 2 ;;
esac
if [ ! -f "$pixel_env" ]; then
    echo "Fichier d'environnement introuvable." >&2
    exit 2
fi
pixel_project=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$pixel_project"
# Un seul fichier -f : combiner les deux fichiers réintroduirait le service web.
# Les chemins hérités sont résolus par Compose depuis le fichier qui les définit.
# Les volumes générés par init_env.py utilisent toujours des chemins absolus.
exec docker compose --env-file "$pixel_env" \
    -f "$pixel_project/docs/reverse-proxy/compose.apache.yml" "$@"
