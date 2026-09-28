#!/usr/bin/env python3
"""Entry point for Django's management commands; see README for Docker usage."""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pixelotheque.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
