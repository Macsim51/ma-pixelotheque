"""Exercise installation preparation with stdlib only and disposable /tmp data.

Run independently of Django: python3 -m unittest tests.test_installation
No test starts Docker, installs dependencies or touches deployed configuration.
"""

import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "init_env.py"
_spec = importlib.util.spec_from_file_location("pixel_installation_script", SCRIPT)
init_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(init_env)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pixelotheque-install-test-", dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.sandbox = Path(self.temporary.name)
        self.private_root = self.sandbox / "private"
        self.uid = os.getuid() or 1000
        self.gid = os.getgid() or 1000

    def run_initializer(self, *arguments, private_root=None):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--private-root", str(private_root or self.private_root),
                "--uid", str(self.uid),
                "--gid", str(self.gid),
                *map(str, arguments),
            ],
            cwd=self.sandbox,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def assert_rejected(self, result):
        self.assertEqual(result.returncode, 2, "L’initialisation aurait dû être refusée.")
        self.assertIn("Initialisation arrêtée", result.stderr)

    def read_config(self, path=None):
        """Read quoted dotenv values as data; never evaluate or source a file."""
        values = {}
        for line in (path or self.private_root / "app.env").read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            self.assertTrue(value.startswith("'") and value.endswith("'"), key)
            values[key] = value[1:-1]
        return values

    def test_creates_private_directories_config_and_unprinted_secret(self):
        result = self.run_initializer()
        self.assertEqual(result.returncode, 0, result.stderr)
        directories = (
            self.private_root,
            self.private_root / "data",
            self.private_root / "media",
            self.private_root / "backups",
            *(self.private_root / "media" / name for name in ("originals", "thumbnails", "previews", "temp")),
        )
        for directory in directories:
            with self.subTest(directory=directory.name):
                self.assertTrue(directory.is_dir())
                info = directory.stat()
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
                self.assertEqual((info.st_uid, info.st_gid), (self.uid, self.gid))
                if directory != self.private_root and directory.name != "media":
                    self.assertEqual(list(directory.iterdir()), [])

        env_file = self.private_root / "app.env"
        info = env_file.stat()
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual((info.st_uid, info.st_gid), (self.uid, self.gid))
        values = self.read_config()
        secret = values.pop("DJANGO_SECRET_KEY")
        self.assertGreaterEqual(len(secret), 64)
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]+", secret), "Format de secret inattendu.")
        self.assertFalse(secret in result.stdout + result.stderr, "Le secret ne doit pas être affiché.")
        self.assertEqual(values, {
            "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "",
            "DJANGO_DEBUG": "False",
            "APP_BASE_PATH": "",
            "APP_HTTPS": "False",
            "TRUST_PROXY_HTTPS": "False",
            "TZ": "Europe/Paris",
            "APP_UID": str(self.uid),
            "APP_GID": str(self.gid),
            "DATA_DIR": str(self.private_root / "data"),
            "MEDIA_DIR": str(self.private_root / "media"),
            "BIND_ADDRESS": "127.0.0.1",
            "WEB_PORT": "8000",
            "PIXEL_IMAGE": "ma-pixelotheque:local",
        })
        self.assertEqual(list(self.private_root.rglob("*.sqlite3")), [])

    def test_custom_prefix_hosts_and_separate_private_env_file(self):
        env_file = self.sandbox / "secrets" / "instance.env"
        hosts = "photos.example.test,192.168.0.100,[::1]"
        result = self.run_initializer(
            "--env-file", env_file,
            "--base-path", "/ma-pixelotheque",
            "--allowed-hosts", hosts,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        values = self.read_config(env_file)
        self.assertEqual(values["DJANGO_ALLOWED_HOSTS"], hosts)
        self.assertEqual(values["APP_BASE_PATH"], "/ma-pixelotheque")
        self.assertFalse((self.private_root / "app.env").exists())
        self.assertEqual(stat.S_IMODE(env_file.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)

    def test_host_list_normalizes_spaces_case_and_final_dns_dot(self):
        result = self.run_initializer(
            "--allowed-hosts", " Photos.Example.Test., LOCALHOST, [::1] "
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.read_config()["DJANGO_ALLOWED_HOSTS"], "photos.example.test,localhost,[::1]"
        )

    def test_existing_secret_is_not_replaced_or_displayed(self):
        first = self.run_initializer()
        self.assertEqual(first.returncode, 0, first.stderr)
        env_file = self.private_root / "app.env"
        before = env_file.read_bytes()
        secret = self.read_config()["DJANGO_SECRET_KEY"]
        second = self.run_initializer("--allowed-hosts", "changed.example.test")
        self.assert_rejected(second)
        self.assertIn("existe déjà", second.stderr)
        self.assertTrue(env_file.read_bytes() == before, "La configuration existante a été modifiée.")
        self.assertFalse(secret in second.stdout + second.stderr, "Le secret ne doit pas être affiché.")

    def test_each_installation_gets_a_distinct_secret(self):
        first = self.run_initializer()
        second_root = self.sandbox / "second-private"
        second = self.run_initializer(private_root=second_root)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue(
            self.read_config()["DJANGO_SECRET_KEY"]
            != self.read_config(second_root / "app.env")["DJANGO_SECRET_KEY"],
            "Deux installations ne doivent pas recevoir le même secret.",
        )

    def test_documentroot_and_project_paths_are_rejected_without_writing(self):
        roots = tuple(root.resolve() for root in init_env.KNOWN_DOCUMENT_ROOTS)
        self.assertIn(PROJECT_ROOT, roots)
        for root in roots:
            for destination in (root, root / "private-instance"):
                with self.subTest(destination=destination), self.assertRaises(ValueError):
                    # Direct validation guarantees this test writes only in /tmp.
                    init_env.private_path(str(destination), roots)

    def test_additional_documentroot_is_rejected_before_creating_anything(self):
        public_root = self.sandbox / "published"
        result = self.run_initializer(
            "--document-root", public_root,
            private_root=public_root / "private-instance",
        )
        self.assert_rejected(result)
        self.assertFalse(public_root.exists())

    def test_common_public_directory_names_are_rejected(self):
        for name in ("www", "public_html", "htdocs", "WWW"):
            with self.subTest(name=name):
                public_path = self.sandbox / name / "instance"
                result = self.run_initializer(private_root=public_path)
                self.assert_rejected(result)
                self.assertFalse(public_path.exists())

    def test_env_file_in_documentroot_is_rejected(self):
        public_root = self.sandbox / "published"
        result = self.run_initializer(
            "--document-root", public_root,
            "--env-file", public_root / "app.env",
        )
        self.assert_rejected(result)
        self.assertFalse(self.private_root.exists())
        self.assertFalse(public_root.exists())

    def test_resolved_symlink_cannot_bypass_documentroot_check(self):
        public_root = self.sandbox / "published"
        public_root.mkdir(mode=0o700)
        alias = self.sandbox / "private-alias"
        alias.symlink_to(public_root, target_is_directory=True)
        result = self.run_initializer(
            "--document-root", public_root,
            private_root=alias / "instance",
        )
        self.assert_rejected(result)
        self.assertEqual(list(public_root.iterdir()), [])

    def test_dangling_env_symlink_is_rejected_without_creating_target(self):
        link = self.sandbox / "app.env"
        target = self.sandbox / "unexpected.env"
        link.symlink_to(target)
        result = self.run_initializer("--env-file", link)
        self.assert_rejected(result)
        self.assertTrue(link.is_symlink())
        self.assertFalse(target.exists())
        self.assertFalse(self.private_root.exists())

    def test_existing_private_root_with_public_permissions_is_not_modified(self):
        self.private_root.mkdir(mode=0o755)
        self.private_root.chmod(0o755)
        if os.geteuid() == 0:
            os.chown(self.private_root, self.uid, self.gid)
        result = self.run_initializer()
        self.assert_rejected(result)
        self.assertEqual(stat.S_IMODE(self.private_root.stat().st_mode), 0o755)
        self.assertFalse((self.private_root / "app.env").exists())

    def test_symlinked_data_directory_is_rejected(self):
        self.private_root.mkdir(mode=0o700)
        target = self.sandbox / "elsewhere"
        target.mkdir(mode=0o700)
        if os.geteuid() == 0:
            os.chown(self.private_root, self.uid, self.gid)
            os.chown(target, self.uid, self.gid)
        (self.private_root / "data").symlink_to(target, target_is_directory=True)
        result = self.run_initializer()
        self.assert_rejected(result)
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse((self.private_root / "app.env").exists())

    def test_relative_or_unquotable_paths_are_rejected(self):
        for path in ("relative-storage", str(self.sandbox / "bad'path"), str(self.sandbox / "bad\npath")):
            with self.subTest(path=repr(path)):
                self.assert_rejected(self.run_initializer(private_root=path))
        self.assert_rejected(self.run_initializer("--env-file", "relative.env"))
        self.assertFalse(self.private_root.exists())

    def test_zero_and_negative_uid_gid_are_rejected(self):
        for option in ("--uid", "--gid"):
            for value in ("0", "-1"):
                with self.subTest(option=option, value=value):
                    self.assert_rejected(self.run_initializer(option, value))
        self.assertFalse(self.private_root.exists())

    @unittest.skipIf(os.geteuid() == 0, "La restriction concerne les utilisateurs non root.")
    def test_non_root_user_cannot_request_another_uid_or_gid(self):
        for option, value in (("--uid", os.getuid() + 1), ("--gid", os.getgid() + 1)):
            with self.subTest(option=option):
                self.assert_rejected(self.run_initializer(option, value))
        self.assertFalse(self.private_root.exists())

    def test_invalid_url_prefixes_are_rejected(self):
        for value in ("ma-pixelotheque", "/ma-pixelotheque/", "/", "//photos", "/../photos", "/photos?debug=1", "/photos espace"):
            with self.subTest(prefix=value):
                self.assert_rejected(self.run_initializer("--base-path", value))
        self.assertFalse(self.private_root.exists())

    def test_invalid_hosts_are_rejected(self):
        for value in (
            "", "*", "http://photos.example.test", "photos.example.test:8000",
            "photos.example.test/path", "photos example.test",
            ",,,", "photos.example.test,", "photos.example.test,,localhost",
            "[::1]:8000", "[bad-ipv6]", "192.168.0.999", "-invalid.example.test",
        ):
            with self.subTest(hosts=value):
                self.assert_rejected(self.run_initializer(f"--allowed-hosts={value}"))
        self.assertFalse(self.private_root.exists())


if __name__ == "__main__":
    unittest.main()
