"""Check the public bootstrap without accepting redirects."""

# ruff: noqa: INP001 - standalone container script

import os
import urllib.request
from http import HTTPStatus


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


request = urllib.request.Request(
    "http://127.0.0.1:8000/api/bootstrap/",
    headers={"Host": os.environ["OPENDB_DOMAIN"], "X-Forwarded-Proto": "https"},
)
with urllib.request.build_opener(NoRedirect).open(request, timeout=4) as response:
    if response.status != HTTPStatus.OK:
        raise SystemExit(1)
