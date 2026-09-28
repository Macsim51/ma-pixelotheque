"""Prépare une nouvelle installation privée ; ne lance ni Docker ni migrations."""

import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets
import stat


PROJECT_ROOT = Path(__file__).resolve().parent.parent
KNOWN_DOCUMENT_ROOTS = (Path("/var/www"), Path("/srv/www"), PROJECT_ROOT)


def explicit_hosts(value):
    hosts = []
    for raw_host in value.split(","):
        host = raw_host.strip().lower()
        if not host:
            raise ValueError("La liste des hôtes ne doit pas contenir de valeur vide.")
        if host.startswith("[") and host.endswith("]"):
            try:
                ipaddress.IPv6Address(host[1:-1])
            except ValueError:
                raise ValueError("Adresse IPv6 invalide dans la liste des hôtes.") from None
        else:
            host = host.removesuffix(".")
            labels = host.split(".")
            if len(labels) == 4 and all(label.isdigit() for label in labels):
                try:
                    ipaddress.IPv4Address(host)
                except ValueError:
                    raise ValueError("Adresse IPv4 invalide dans la liste des hôtes.") from None
            if len(host) > 253 or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels
            ):
                raise ValueError("Utiliser des hôtes explicites sans schéma, port ni joker.")
        hosts.append(host)
    return ",".join(hosts)


def private_path(value, document_roots):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Les chemins de stockage doivent être absolus.")
    path = path.resolve()
    if any(path == root or root in path.parents for root in document_roots):
        raise ValueError(f"Destination refusée dans un documentroot ou le dépôt : {path}")
    if any(part.lower() in {"www", "public_html", "htdocs"} for part in path.parts):
        raise ValueError(f"Destination potentiellement publiée par un serveur web : {path}")
    if any(char in str(path) for char in "\n\r'"):
        raise ValueError("Les chemins ne doivent pas contenir de retour à la ligne ni d'apostrophe.")
    return path


def private_directory(path, uid, gid):
    if path.is_symlink():
        raise ValueError(f"Un dossier de données ne doit pas être un lien symbolique : {path}")
    if path.exists():
        info = path.stat()
        if not path.is_dir() or info.st_uid != uid or info.st_gid != gid:
            raise ValueError(f"Le dossier existant doit appartenir à {uid}:{gid} : {path}")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError(f"Le dossier existant doit être privé (mode 700) : {path}")
        return
    path.mkdir(mode=0o700, parents=True)
    if os.geteuid() == 0:
        os.chown(path, uid, gid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", required=True, help="Dossier privé hors de toute racine web")
    parser.add_argument("--env-file", help="Par défaut : PRIVATE_ROOT/app.env")
    parser.add_argument("--document-root", action="append", default=[], help="Racine web supplémentaire à interdire")
    parser.add_argument("--allowed-hosts", default="localhost,127.0.0.1")
    parser.add_argument("--base-path", default="")
    parser.add_argument("--uid", type=int, default=os.getuid() or 1000)
    parser.add_argument("--gid", type=int, default=os.getgid() or 1000)
    args = parser.parse_args()

    try:
        if args.uid < 1 or args.gid < 1:
            raise ValueError("Les conteneurs doivent utiliser un UID et un GID non root.")
        if os.geteuid() != 0 and (args.uid != os.getuid() or args.gid != os.getgid()):
            raise ValueError("Utiliser votre UID/GID, ou exécuter explicitement en administrateur.")
        if not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*", args.base_path):
            raise ValueError("Le préfixe doit être vide ou comme /ma-pixelotheque, sans slash final.")
        allowed_hosts = explicit_hosts(args.allowed_hosts)

        roots = tuple(root.resolve() for root in KNOWN_DOCUMENT_ROOTS) + tuple(
            Path(root).expanduser().resolve() for root in args.document_root
        )
        private_root = private_path(args.private_root, roots)
        requested_env_file = Path(args.env_file or str(private_root / "app.env")).expanduser()
        if requested_env_file.is_symlink():
            raise ValueError("Le fichier d'environnement ne doit pas être un lien symbolique.")
        env_file = private_path(str(requested_env_file), roots)
        if env_file.exists() or env_file.is_symlink():
            raise ValueError("Le fichier d'environnement existe déjà ; aucun secret n'a été remplacé.")

        os.umask(0o077)
        private_directory(private_root, args.uid, args.gid)
        private_directory(env_file.parent, args.uid, args.gid)
        for name in ("data", "media", "backups"):
            private_directory(private_root / name, args.uid, args.gid)
        for name in ("originals", "thumbnails", "previews", "temp"):
            private_directory(private_root / "media" / name, args.uid, args.gid)

        values = {
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
            "DJANGO_ALLOWED_HOSTS": allowed_hosts,
            "DJANGO_CSRF_TRUSTED_ORIGINS": "",
            "DJANGO_DEBUG": "False",
            "APP_BASE_PATH": args.base_path,
            "APP_HTTPS": "False",
            "TRUST_PROXY_HTTPS": "False",
            "TZ": "Europe/Paris",
            "APP_UID": str(args.uid),
            "APP_GID": str(args.gid),
            "DATA_DIR": str(private_root / "data"),
            "MEDIA_DIR": str(private_root / "media"),
            "BIND_ADDRESS": "127.0.0.1",
            "WEB_PORT": "8000",
            "PIXEL_IMAGE": "ma-pixelotheque:local",
        }
        descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("# Configuration privée — ne pas publier ni ajouter à Git.\n")
            for key, value in values.items():
                stream.write(f"{key}='{value}'\n")
        if os.geteuid() == 0:
            os.chown(env_file, args.uid, args.gid)
    except (OSError, ValueError) as error:
        parser.exit(2, f"Initialisation arrêtée : {error}\n")

    print(f"Configuration privée créée : {env_file}")
    print(f"Volumes préparés pour UID:GID {args.uid}:{args.gid}.")
    print("Aucun secret affiché. Aucun conteneur démarré. Vérifier les paramètres avant le build.")


if __name__ == "__main__":
    main()
