"""Exercise the final image through real HTTP, cookies, CSRF and a URL prefix.

Run inside an ephemeral container: ``python tests/smoke_http.py``. The script
uses only the standard library; Django and Gunicorn run in subprocesses from
the image. Both listeners bind to container loopback, all data lives in /tmp,
and no existing database, secret or collected static directory is used.
"""

from contextlib import contextmanager
from html.parser import HTMLParser
import http.client
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener
import zlib


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PREFIX = "/ma-pixelotheque"
BACKEND_PORT = 18080
PROXY_PORT = 18081
ORIGIN = f"http://127.0.0.1:{PROXY_PORT}"
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class PrefixProxy(BaseHTTPRequestHandler):
    """Simulate the deployed proxy: strip only PREFIX and preserve the Host."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        if self.path == PREFIX:
            self.send_response(301)
            self.send_header("Location", PREFIX + "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self.path.startswith(PREFIX + "/"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > 65536:
            self.send_error(413)
            return
        payload = self.rfile.read(length) if length else None
        # No untrusted forwarding headers are inherited from the browser.
        headers = {
            name: value for name, value in self.headers.items()
            if name.lower() not in HOP_HEADERS
            and not name.lower().startswith("x-forwarded-")
        }
        headers["Connection"] = "close"
        upstream = http.client.HTTPConnection("127.0.0.1", BACKEND_PORT, timeout=10)
        try:
            upstream.request(self.command, self.path[len(PREFIX):], body=payload, headers=headers)
            response = upstream.getresponse()
            body = response.read()
            self.send_response(response.status)
            # Retain individual Set-Cookie headers and their original Path.
            for name, value in response.getheaders():
                if name.lower() not in HOP_HEADERS | {"content-length", "server", "date"}:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (OSError, http.client.HTTPException):
            self.send_error(502)
        finally:
            upstream.close()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class Page(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.assets = {}
        self.elements = {}
        self.local_targets = []
        self.current_form = None
        self.feed(body.decode("utf-8"))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.elements[attrs["id"]] = attrs
        for name in ("href", "src", "action"):
            target = attrs.get(name, "")
            if target.startswith("/"):
                self.local_targets.append(target)
        if tag == "form":
            self.current_form = {"action": attrs.get("action", ""), "fields": {}}
            self.forms.append(self.current_form)
        elif tag in {"input", "select", "textarea", "button"} and self.current_form is not None:
            if attrs.get("name"):
                self.current_form["fields"][attrs["name"]] = attrs.get("value", "")
        if tag == "link" and attrs.get("rel") in {"stylesheet", "icon"}:
            self.assets[attrs["rel"]] = attrs.get("href", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current_form = None

    def form(self, field):
        for form in self.forms:
            if field in form["fields"]:
                return form
        raise AssertionError(f"Formulaire introuvable : {field}")

    def assert_prefixes(self):
        for target in self.local_targets:
            # Django Admin's "View site" points to SCRIPT_NAME without a slash.
            require(target == PREFIX or target.startswith(PREFIX + "/"), f"Lien sans préfixe : {target}")


class Browser:
    def __init__(self):
        self.cookies = CookieJar()
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(self.cookies), NoRedirect())

    def request(self, path, fields=None):
        data = urlencode(fields).encode("utf-8") if fields is not None else None
        return self.send(path, data=data)

    def multipart(self, path, fields, *, filename, content):
        boundary = "pixelotheque-" + secrets.token_hex(16)
        chunks = []
        for name, value in fields.items():
            chunks.append((
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
            ).encode("utf-8"))
        chunks.extend([
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n").encode("ascii"),
            content, f"\r\n--{boundary}--\r\n".encode("ascii"),
        ])
        return self.send(path, data=b"".join(chunks), extra_headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        })

    def send(self, path, *, data=None, extra_headers=None):
        require(path.startswith(PREFIX + "/"), f"Requête navigateur hors préfixe : {path}")
        headers = {"Origin": ORIGIN, "Referer": ORIGIN + path} if data is not None else {}
        headers.update(extra_headers or {})
        request = Request(ORIGIN + path, data=data, headers=headers)
        try:
            response = self.opener.open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def page(self, path):
        status, _, body = self.request(path)
        require(status == 200, f"GET {path} : HTTP {status}, attendu 200")
        page = Page(body)
        page.assert_prefixes()
        return page, body


def redirected(status, headers, expected=None):
    require(status == 302, f"Redirection attendue, reçu HTTP {status}")
    target = headers.get("Location", "")
    parsed = urlsplit(target)
    require(not parsed.netloc and target.startswith(PREFIX + "/"), f"Redirection incorrecte : {target}")
    if expected is not None:
        require(target == expected, f"Destination incorrecte : {target}, attendu {expected}")
    return target


def run_manage(env, *args):
    result = subprocess.run(
        [sys.executable, "manage.py", *args], cwd=PROJECT_ROOT, env=env,
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"manage.py {args[0]} a échoué :\n{result.stderr[-6000:]}")


@contextmanager
def gunicorn(env, log_path):
    with log_path.open("ab") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "gunicorn", "pixelotheque.wsgi:application",
             f"--bind=127.0.0.1:{BACKEND_PORT}", "--workers=1", "--threads=2",
             "--timeout=30", "--graceful-timeout=5", "--worker-tmp-dir=/tmp",
             "--error-logfile=-"],
            cwd=PROJECT_ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 20
            while True:
                require(process.poll() is None, "Gunicorn s’est arrêté pendant son démarrage")
                connection = http.client.HTTPConnection("127.0.0.1", BACKEND_PORT, timeout=2)
                try:
                    connection.request("GET", "/healthz")
                    response = connection.getresponse()
                    body = response.read()
                    require(response.status == 200, f"Health interne : HTTP {response.status}, attendu 200")
                    require(json.loads(body) == {"status": "ok"}, "Réponse health interne incorrecte")
                    break
                except (OSError, http.client.HTTPException):
                    require(time.monotonic() < deadline, "Gunicorn ne répond pas après 20 secondes")
                    time.sleep(0.1)
                finally:
                    connection.close()
            yield
        except BaseException:
            print(log_path.read_text(errors="replace")[-6000:], file=sys.stderr)
            raise
        finally:
            # The separate process group also covers a worker whose master
            # unexpectedly exited before cleanup.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)


def login(browser, username, password):
    path = PREFIX + "/accounts/login/"
    page, _ = browser.page(path)
    form = page.form("username")
    require(form["action"] == path, "L’action du formulaire de connexion doit conserver le préfixe")
    token = form["fields"].get("csrfmiddlewaretoken")
    require(bool(token), "Jeton CSRF absent du formulaire de connexion")
    status, headers, _ = browser.request(path, {"username": username, "password": password, "csrfmiddlewaretoken": token})
    redirected(status, headers, PREFIX + "/albums/")
    session = next((cookie for cookie in browser.cookies if cookie.name == "pixelotheque_sessionid"), None)
    require(session is not None, "Cookie de session absent")
    require(session.path == PREFIX + "/", "Le cookie de session doit être limité au préfixe")


def synthetic_png():
    """Build a valid 16×12 RGB PNG without requiring Pillow in this script."""
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 16, 12, 8, 2, 0, 0, 0)
    pixels = (b"\x00" + bytes((35, 125, 190)) * 16) * 12
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def check_photo_and_share(browser, album_path, env):
    """Exercise upload and worker outputs before exposing them through a link."""
    album_id = album_path.rstrip("/").rsplit("/", 1)[1]
    upload_path = PREFIX + "/upload/"
    upload_page, _ = browser.page(upload_path + "?album=" + album_id)
    upload_form = upload_page.form("files")
    original = synthetic_png()
    status, _, body = browser.multipart(upload_path, {
        "csrfmiddlewaretoken": upload_form["fields"]["csrfmiddlewaretoken"],
        "album": album_id,
    }, filename="souvenir-http.png", content=original)
    require(status == 200, f"Upload multipart : HTTP {status}, attendu 200")
    result = json.loads(body)
    require(not result["errors"] and len(result["uploaded"]) == 1, "L’upload PNG doit créer exactement une photo")
    require(result["album_id"] == album_id, "L’upload doit conserver l’album choisi")
    media_id = result["uploaded"][0]["id"]
    original_path = f"{PREFIX}/files/{media_id}/original/"
    require(browser.request(original_path)[0] == 404, "Un original non validé ne doit pas être accessible")

    # --once consumes exactly one job: INSPECT schedules RENDER, which the
    # second command consumes. Both commands use the same temporary DB/media.
    run_manage(env, "process_jobs", "--once")
    run_manage(env, "process_jobs", "--once")
    status, headers, downloaded = browser.request(original_path)
    require(status == 200, f"Original après traitement : HTTP {status}")
    require(downloaded == original, "Les octets de l’original doivent rester intacts")
    require(headers.get("Content-Disposition", "").startswith("attachment"), "L’original doit être servi en téléchargement")
    status, headers, thumbnail = browser.request(f"{PREFIX}/files/{media_id}/thumbnail/")
    require(status == 200 and headers.get("Content-Type", "").startswith("image/webp"), "La miniature WebP doit être disponible")
    require(thumbnail.startswith(b"RIFF") and thumbnail[8:12] == b"WEBP", "Le worker doit produire une vraie image WebP")

    photo_path = f"{PREFIX}/photos/{media_id}/"
    photo_page, body = browser.page(photo_path)
    require(b"souvenir-http.png" in body, "La lightbox doit afficher la photo envoyée")
    browser.page(PREFIX + "/photos/")
    favorite = next(form for form in photo_page.forms if form["action"] == photo_path + "favorite/")
    status, headers, _ = browser.request(favorite["action"], favorite["fields"])
    redirected(status, headers)
    _, favorites_body = browser.page(PREFIX + "/favorites/")
    require(media_id.encode() in favorites_body, "La photo marquée doit apparaître dans les favoris")

    manage_path = f"{PREFIX}/s/manage/{album_id}/"
    manage_page, _ = browser.page(manage_path)
    share_form = manage_page.form("expires_at")
    status, headers, body = browser.request(manage_path, {
        "csrfmiddlewaretoken": share_form["fields"]["csrfmiddlewaretoken"],
    })
    require(status == 200, "Le propriétaire famille doit pouvoir créer un lien")
    require(headers.get("Referrer-Policy") == "no-referrer", "L’URL créée ne doit pas fuiter dans les référents")
    created_page = Page(body)
    shared_url = created_page.elements.get("created-link", {}).get("value", "")
    parsed = urlsplit(shared_url)
    require(parsed.scheme == "http" and parsed.netloc == urlsplit(ORIGIN).netloc, "Le lien doit préserver le Host du proxy")
    require(parsed.path.startswith(PREFIX + "/s/"), "Le lien doit contenir le préfixe")
    token = parsed.path.rstrip("/").rsplit("/", 1)[1]
    require(len(token) == 43, "Le lien doit contenir un token aléatoire complet")
    manage_page, body = browser.page(manage_path)
    require(token.encode() not in body, "Le token ne doit pas être réaffiché par un GET ultérieur")

    anonymous = Browser()
    anonymous.page(parsed.path)
    status, headers, shared_thumbnail = anonymous.request(f"{parsed.path}files/{media_id}/thumbnail/")
    require(status == 200 and shared_thumbnail == thumbnail, "Un invité anonyme doit voir la miniature partagée")
    require(headers.get("Referrer-Policy") == "no-referrer", "Le fichier partagé doit protéger le référent")
    require("no-store" in headers.get("Cache-Control", ""), "Le fichier partagé ne doit pas être conservé en cache")
    require(anonymous.request(f"{parsed.path}files/{media_id}/original/")[0] == 404, "Le partage ne doit pas autoriser l’original par défaut")
    require(anonymous.request(PREFIX + "/photos/")[0] == 302, "Le lien ne doit pas authentifier son visiteur")
    revoke = next(form for form in manage_page.forms if form["action"].endswith("/revoke/"))
    status, headers, _ = browser.request(revoke["action"], revoke["fields"])
    redirected(status, headers, manage_path)
    require(anonymous.request(parsed.path)[0] == 404, "Un lien révoqué doit devenir indisponible immédiatement")
    require(anonymous.request(f"{parsed.path}files/{media_id}/thumbnail/")[0] == 404, "La révocation doit aussi interdire une miniature déjà visitée")
    print("Photos validées : upload multipart, inspect/render, original intact, WebP, favoris et partage révoqué.")
    return media_id


def check_album_hierarchy(browser, existing_path, media_id):
    """Create, group and move albums through real CSRF-protected forms."""
    create_path = PREFIX + "/albums/new/"
    page, _ = browser.page(create_path)
    csrf = page.form("title")["fields"]["csrfmiddlewaretoken"]
    status, headers, _ = browser.request(create_path, {
        "csrfmiddlewaretoken": csrf, "title": "Zoo HTTP", "visibility": "family",
    })
    parent_path = redirected(status, headers)
    parent_id = parent_path.rstrip("/").rsplit("/", 1)[1]
    child_create_path = create_path + "?parent=" + parent_id
    page, _ = browser.page(child_create_path)
    status, headers, _ = browser.request(child_create_path, {
        "csrfmiddlewaretoken": page.form("title")["fields"]["csrfmiddlewaretoken"],
        "title": "Zoo de Beauval HTTP", "visibility": "family", "parent": parent_id,
    })
    child_path = redirected(status, headers)
    child_page, _ = browser.page(child_path)
    require(parent_path in child_page.local_targets, "Le fil d’Ariane doit mener au parent")

    group_path = parent_path + "group/"
    page, _ = browser.page(group_path)
    existing_id = existing_path.rstrip("/").rsplit("/", 1)[1]
    status, headers, _ = browser.request(group_path, {
        "csrfmiddlewaretoken": page.form("albums")["fields"]["csrfmiddlewaretoken"],
        "albums": existing_id,
    })
    redirected(status, headers, parent_path)
    parent_page, _ = browser.page(parent_path)
    require(existing_path in parent_page.local_targets and child_path in parent_page.local_targets,
            "Le parent doit présenter les albums créés et déplacés")
    root_page, _ = browser.page(PREFIX + "/albums/")
    require(parent_path in root_page.local_targets, "Le parent doit rester au premier niveau")
    require(existing_path not in root_page.local_targets and child_path not in root_page.local_targets,
            "Les sous-albums ne doivent plus encombrer le premier niveau")
    require(f"{PREFIX}/files/{media_id}/thumbnail/" in root_page.local_targets,
            "Le parent doit avoir une vignette automatique issue de son sous-album")

    cover_path = parent_path + "cover/"
    cover_page, body = browser.page(cover_path)
    require(media_id.encode() in body, "La galerie de vignettes doit inclure les photos des sous-albums")
    status, headers, _ = browser.request(cover_path, {
        "csrfmiddlewaretoken": cover_page.form("cover_photo")["fields"]["csrfmiddlewaretoken"],
        "cover_photo": media_id,
    })
    redirected(status, headers, parent_path)
    cover_page, body = browser.page(cover_path)
    require("Vignette personnalisée".encode() in body, "Le choix de vignette doit être conservé")
    status, headers, _ = browser.request(cover_path, {
        "csrfmiddlewaretoken": cover_page.form("cover_photo")["fields"]["csrfmiddlewaretoken"],
        "cover_photo": "",
    })
    redirected(status, headers, parent_path)
    _, body = browser.page(cover_path)
    require("Vignette personnalisée".encode() not in body, "Le retour au mode automatique doit être conservé")
    print("Vignettes validées : photo du sous-album, sélection et retour au mode automatique.")

    edit_path = existing_path + "edit/"
    page, _ = browser.page(edit_path)
    status, headers, _ = browser.request(edit_path, {
        "csrfmiddlewaretoken": page.form("title")["fields"]["csrfmiddlewaretoken"],
        "title": "Album témoin HTTP", "visibility": "family", "parent": "",
        "allow_family_uploads": "on",
    })
    redirected(status, headers, existing_path)
    root_page, _ = browser.page(PREFIX + "/albums/")
    require(existing_path in root_page.local_targets, "Un album déplacé à la racine doit y réapparaître")
    print("Sous-albums validés : création, rangement, fil d’Ariane et retour au premier niveau.")


def check_http(password, env):
    browser = Browser()
    status, headers, _ = browser.request(PREFIX + "/")
    require(redirected(status, headers).startswith(PREFIX + "/accounts/login/"), "L’accueil anonyme doit ouvrir la connexion")
    page, _ = browser.page(PREFIX + "/accounts/login/")
    for kind, expected_type in (("stylesheet", "text/css"), ("icon", "image/svg+xml")):
        asset = page.assets.get(kind)
        require(bool(asset), f"Ressource {kind} absente")
        status, headers, body = browser.request(asset)
        require(status == 200 and bool(body), f"Ressource {kind} indisponible : HTTP {status}")
        require(headers.get("Content-Type", "").startswith(expected_type), f"Type incorrect pour {kind}")

    # A real middleware rejection must happen before authentication.
    status, _, _ = Browser().request(PREFIX + "/accounts/login/", {"username": "smoke-family", "password": password})
    require(status == 403, "La connexion sans jeton CSRF doit être refusée")
    login(browser, "smoke-family", password)
    browser.page(PREFIX + "/albums/")
    page, _ = browser.page(PREFIX + "/albums/new/")
    form = page.form("title")
    status, headers, _ = browser.request(PREFIX + "/albums/new/", {
        "csrfmiddlewaretoken": form["fields"]["csrfmiddlewaretoken"],
        "title": "Album témoin HTTP", "description": "Créé par le test HTTP isolé.",
        "visibility": "family", "allow_family_uploads": "on",
    })
    detail_path = redirected(status, headers)
    require(detail_path.startswith(PREFIX + "/albums/"), "Destination de l’album incorrecte")
    page, body = browser.page(detail_path)
    require("Album témoin HTTP".encode() in body, "L’album créé doit être visible après redirection")
    media_id = check_photo_and_share(browser, detail_path, env)
    check_album_hierarchy(browser, detail_path, media_id)
    status, _, _ = browser.request(PREFIX + "/admin/")
    require(status == 302, "Le compte famille ne doit pas accéder à l’administration")
    status, _, _ = browser.request(PREFIX + "/accounts/logout/")
    require(status == 405, "La déconnexion GET doit être refusée")
    logout = next(form for form in page.forms if form["action"] == PREFIX + "/accounts/logout/")
    status, headers, _ = browser.request(logout["action"], {"csrfmiddlewaretoken": logout["fields"]["csrfmiddlewaretoken"]})
    redirected(status, headers, PREFIX + "/accounts/login/")

    admin = Browser()
    login(admin, "smoke-admin", password)
    admin.page(PREFIX + "/admin/")
    status, headers, body = admin.request(PREFIX + "/healthz")
    require(status == 200 and json.loads(body) == {"status": "ok"}, "Health via proxy incorrect")
    require("no-store" in headers.get("Cache-Control", ""), "Health ne doit pas être mis en cache")
    print("HTTP validé : connexion/CSRF, cookies, album, redirections, statique, admin, health.")


def check_https_health():
    # Both the readiness probe above and this proxied call omit forwarded proto.
    # Django's request.path still contains PREFIX although PATH_INFO is stripped.
    browser = Browser()
    status, _, body = browser.request(PREFIX + "/healthz")
    require(status == 200 and json.loads(body) == {"status": "ok"}, "Health doit rester disponible en HTTP lorsque HTTPS est activé")
    status, headers, _ = browser.request(PREFIX + "/accounts/login/")
    require(status == 301, "HTTPS doit cependant être imposé aux pages utilisateur")
    require(headers.get("Location") == ORIGIN.replace("http:", "https:") + PREFIX + "/accounts/login/", "Redirection HTTPS incorrecte")
    print("HTTPS validé : health sans en-tête proxy et redirection HTTPS des pages utilisateur.")


def main():
    with tempfile.TemporaryDirectory(prefix="pixelotheque-http-") as directory:
        temporary = Path(directory)
        password = secrets.token_urlsafe(24) + "Z7!"
        env = os.environ.copy()
        env.update({
            "DJANGO_SETTINGS_MODULE": "pixelotheque.settings",
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
            "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "", "DJANGO_DEBUG": "False",
            "APP_BASE_PATH": PREFIX, "APP_HTTPS": "False", "TRUST_PROXY_HTTPS": "False",
            "DATABASE_PATH": str(temporary / "smoke.sqlite3"),
            "MEDIA_ROOT": str(temporary / "media"), "STATIC_ROOT": str(temporary / "static"),
            "SMOKE_ACCOUNT_PASSWORD": password, "PYTHONDONTWRITEBYTECODE": "1",
            "GUNICORN_CMD_ARGS": "", "TZ": "Europe/Paris",
        })
        env.pop("DATABASE_TEST_PATH", None)
        run_manage(env, "migrate", "--noinput", "--verbosity=0")
        run_manage(env, "collectstatic", "--noinput", "--verbosity=0")
        run_manage(env, "shell", "-c", (
            "import os; from django.contrib.auth import get_user_model; "
            "User = get_user_model(); password = os.environ['SMOKE_ACCOUNT_PASSWORD']; "
            "User.objects.create_user('smoke-family', password=password, role='family'); "
            "User.objects.create_superuser('smoke-admin', password=password)"
        ))
        env.pop("SMOKE_ACCOUNT_PASSWORD")
        proxy = HTTPServer(("127.0.0.1", PROXY_PORT), PrefixProxy)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            with gunicorn(env, temporary / "gunicorn.log"):
                check_http(password, env)
            env.update({"APP_HTTPS": "True", "TRUST_PROXY_HTTPS": "True"})
            with gunicorn(env, temporary / "gunicorn-https.log"):
                check_https_health()
        finally:
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=5)
    print("Smoke HTTP terminé ; processus arrêtés et données temporaires supprimées.")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, RuntimeError, OSError, URLError, subprocess.SubprocessError) as error:
        print(f"ÉCHEC du smoke HTTP : {error}", file=sys.stderr)
        sys.exit(1)
