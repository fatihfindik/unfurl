"""Build the extension for a deployed Unfurl (#64, D-199):

    uv run python scripts/build_extension.py https://unfurl.example.com

Copies extension/ to dist/extension/ with the deployed address in config.js and as
the only host permission in manifest.json. The address must be https:// and not a
local one: a production extension that talks plain http would send the sign-in
password and the token unencrypted. The "key" (it pins the id of the unpacked
development extension) is left out: the Chrome Web Store gives the extension its own
id, which then goes into EXTENSION_ORIGINS (D-137).

extension/ itself stays as it is, pointing at the personal instance (D-155).
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "extension"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
# Development notes, not part of the shipped extension.
LEFT_OUT = {"README.md", "TESTING.md"}


class BuildError(Exception):
    """The address can't be used for a production build."""


def check_base_url(url: str) -> str:
    """The address without a trailing slash, or BuildError."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise BuildError("The address must start with https://")
    if parts.hostname in LOCAL_HOSTS or parts.hostname.endswith(".localhost"):
        raise BuildError("The address must not be a local one.")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise BuildError("Give only the site's address, e.g. https://unfurl.example.com")
    return f"https://{parts.netloc}"


def build(base_url: str, out: Path, source: Path = SOURCE) -> Path:
    base = check_base_url(base_url)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(source, out, ignore=shutil.ignore_patterns(*LEFT_OUT))

    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["host_permissions"] = [f"{base}/*"]
    manifest.pop("key", None)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    config_path = out / "config.js"
    config, count = re.subn(
        r'export const APP_BASE_URL = "[^"]*";',
        f'export const APP_BASE_URL = "{base}";',
        config_path.read_text(),
    )
    if count != 1:
        raise BuildError("config.js has no APP_BASE_URL line to replace.")
    config_path.write_text(config)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the extension for a deployed Unfurl.")
    parser.add_argument("base_url", help="the deployed address, e.g. https://unfurl.example.com")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "extension")
    args = parser.parse_args(argv)
    try:
        out = build(args.base_url, args.out)
    except BuildError as error:
        print(f"Not built: {error}", file=sys.stderr)
        return 1
    print(f"Built {out}. Zip this folder for the Chrome Web Store.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
