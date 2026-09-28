"""Copie cohérente base + médias, après arrêt explicite des services écrivains."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3


def reject_symlinks(directory, names):
    if any((Path(directory) / name).is_symlink() for name in names):
        raise ValueError("Un lien symbolique a été trouvé dans les médias ; copie arrêtée.")
    return []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", help="Nouveau dossier de sauvegarde, chemin absolu")
    parser.add_argument("--services-stopped", action="store_true", help="Confirme que web, worker et les traitements ponctuels sont arrêtés")
    parser.add_argument("--label", default="", help="Version Git/image à retrouver lors de la restauration")
    args = parser.parse_args()
    if not args.services_stopped:
        parser.error("Arrêter les services écrivains puis passer --services-stopped ; ce script ne les arrête pas.")

    database = Path(os.environ.get("DATABASE_PATH", "/data/db.sqlite3")).resolve()
    media = Path(os.environ.get("MEDIA_ROOT", "/media")).resolve()
    destination = Path(args.destination)
    try:
        if not destination.is_absolute():
            raise ValueError("La destination doit être un chemin absolu.")
        destination = destination.resolve()
        if any(destination == source or source in destination.parents for source in (database.parent, media)):
            raise ValueError("La sauvegarde doit se trouver hors des volumes source.")
        if not database.is_file() or not media.is_dir():
            raise ValueError("Base ou dossier médias introuvable ; vérifier les volumes.")
        os.umask(0o077)
        destination.mkdir(mode=0o700, parents=False, exist_ok=False)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(destination / "db.sqlite3")) as target:
                source.backup(target, pages=256)
                # La copie livrée est un fichier autonome, même si WAL est
                # adopté plus tard sur la base source.
                target.execute("PRAGMA journal_mode=DELETE")
                if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("Le contrôle de la copie SQLite a échoué.")
        shutil.copytree(media, destination / "media", ignore=reject_symlinks)
        # Écrit en dernier : son absence identifie une copie interrompue.
        manifest = {
            "format": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": args.label,
            "sqlite_version": sqlite3.sqlite_version,
            "database": "db.sqlite3",
            "media": "media",
            "secrets_included": False,
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Sauvegarde incomplète : {error}\nNe pas restaurer cette tentative.\n")
    print(f"Sauvegarde terminée : {destination}")
    print("Le fichier app.env est à sauvegarder séparément dans un emplacement privé.")


if __name__ == "__main__":
    main()
