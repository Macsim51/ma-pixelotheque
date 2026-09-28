"""Keep bearer tokens out of application logs, including exception messages."""

import logging
import re


SHARE_PATH = re.compile(r"(/s/)(?!manage(?:/|\s|$))[^/\s?'\"<>]+")


class PrivateDataFormatter(logging.Formatter):
    """Sanitize the final formatted record, so traceback text is covered too.

    The reverse proxy has its own access logger; its configuration must apply
    equivalent filtering. This formatter cannot protect logs of other services.
    """

    def format(self, record):
        return SHARE_PATH.sub(r"\1[partage]", super().format(record))
