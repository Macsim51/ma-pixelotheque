#!/bin/sh
# Ne charge jamais le fichier d'environnement comme un script shell.
set -eu

if [ "$#" -lt 2 ]; then
    echo "Usage: sh scripts/compose.sh /chemin/prive/app.env [options Compose] commande" >&2
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
exec docker compose --env-file "$pixel_env" -f "$pixel_project/docker-compose.yml" "$@"
