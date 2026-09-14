"""Install the exact model atomically; never disclose the private download URL."""

# ruff: noqa: INP001, TRY301 - standalone script with one redacted failure path

import hashlib
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler
from urllib.request import build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


urlopen = build_opener(NoRedirect).open

MODEL = Path("/models/granite-97m-r2-q8_0.gguf")
SHA256 = "d0aefc589e25df26b75d45eaa3b7205cad850ceeaaef82c6b98b04b166b992d4"


def valid(path):
    if not path.is_file() or path.is_symlink():
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == SHA256


def main():
    temporary = MODEL.with_suffix(".download")
    try:
        if valid(MODEL):
            MODEL.chmod(0o444)
            return 0
        url = os.environ.get("OPENDB_MODEL_URL", "")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
            raise ValueError
        temporary.unlink(missing_ok=True)
        with urlopen(url, timeout=60) as response, temporary.open("xb") as output:
            if urlsplit(response.url).scheme != "https":
                raise ValueError
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if not valid(temporary):
            raise ValueError
        temporary.chmod(0o444)
        temporary.replace(MODEL)
    except Exception:  # noqa: BLE001 - exception messages can contain signed URLs.
        temporary.unlink(missing_ok=True)
        sys.stderr.write(
            "Model initialization failed (download or SHA256 verification).\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
