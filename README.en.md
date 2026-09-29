# Ma Pixelothèque

A small, self-hosted family photo library designed for a Raspberry Pi 5 with
4 GB RAM. Server-rendered Django pages, plain CSS, lightweight JavaScript and
SQLite. No Node.js or frontend build pipeline.

[Documentation française](README.md)

## Current status

Initial photo MVP. Features include multiple JPEG/PNG/WebP uploads,
background EXIF extraction and thumbnails, family/private/restricted albums,
assigning a photo to multiple albums,
nesting albums with breadcrumbs and moving existing albums into a parent,
custom album thumbnails selected from the album or its subalbums,
a paginated timeline, geographic clusters on a Leaflet map, search, personal
favorites, metadata editing and revocable private album links. The interface
supports light, dark and system themes and is currently in French.

The initial target is about 5,000 photos. Tests include 5,000 synthetic database
entries; this is not a throughput benchmark of real image files. See the
[architecture document](docs/architecture.md) for design choices and limits.

## Install with Docker Compose

Requirements: Linux ARM64 or x86-64, Docker, a recent Docker Compose plugin and
Python 3 on the host for the configuration helper. Application dependencies
are installed inside the Docker image, not in the host Python environment.

Clone the repository outside any web server's document root, then run:

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
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) on the host. Use an SSH
tunnel for remote access or configure a [reverse proxy](docs/reverse-proxy/README.md).
The default port is bound to loopback. `/admin/` lets the administrator create
family and guest accounts; public sign-up is disabled.

The helper creates private data directories and a random secret without printing
it. It refuses to replace an existing configuration. Store secrets, the database,
media and backups outside the repository and all publicly served directories.
The application supports deployment under a prefix such as `/ma-pixelotheque/`.

## Permissions

- Family members can create albums. Family albums are visible to other family
  accounts; guests require an explicit invitation.
- Private albums belong to their creator and administrators. Restricted albums
  also allow explicitly selected accounts.
- Only an album's family owner or an administrator can change its settings.
- Administration requires an active Django superuser; staff status alone is
  insufficient. A newly created ordinary account defaults to guest.

## Development and operations

See [contribution instructions](CONTRIBUTING.md),
[deployment and updates](docs/deployment.md),
[backup and restore](docs/backup-restore.md), and
[security reporting](SECURITY.md). Detailed guides are currently in French.

The same Docker image can run the test suite without installing development
packages on the host. Tests use a temporary SQLite file and synthetic accounts.
Compose starts a web service and a separate Pillow worker, processing one photo
at a time. Image decoding runs in a disposable child with memory and time limits;
originals are mounted read-only in the worker. Docker resource limits also need
kernel support on the host. SQLite stores the queue; Redis and Celery are not
required. PostgreSQL support is a future extension.

Private links are shown only once, with only a hash stored in the database.
Disabling family sharing removes families' link management rights; administrators
can still manage and revoke existing links. Preview files omit EXIF/GPS, while
downloaded originals retain their original metadata. The map uses external
OpenStreetMap tiles by default. HTMX and Leaflet code is served locally.

Video, folder scanning, AI and native mobile apps are outside this MVP.
Permanent photo deletion is not implemented; deleting an album that would leave
a photo without any album is prevented.

Original project: [Ma Pixelothèque by Macsim51](https://github.com/Macsim51/ma-pixelotheque).
Released under the [MIT License](LICENSE). Keep the copyright and permission
notice when redistributing the software. Third-party components retain their
respective licenses.
