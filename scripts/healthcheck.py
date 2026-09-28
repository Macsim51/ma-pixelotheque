"""Probe interne : le proxy externe retire déjà APP_BASE_PATH."""

from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main():
    try:
        with urlopen("http://127.0.0.1:8000/healthz", timeout=3) as response:
            if response.status != 200:
                raise SystemExit(1)
    except (HTTPError, URLError, TimeoutError, OSError):
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
