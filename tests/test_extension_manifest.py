"""The extension's manifest asks for no more than SPEC §7.2 and §7.3 allow (D-137, D-139).

The extension itself has no automated tests (SPEC §13); extension/TESTING.md is the
manual list. This checks the permissions, which decide what it *could* do.
"""

import base64
import hashlib
import json
from pathlib import Path

EXTENSION = Path(__file__).parent.parent / "extension"
MANIFEST = json.loads((EXTENSION / "manifest.json").read_text())


def extension_id(key: str) -> str:
    """Chrome's id for an extension with this public key: the first 32 hex digits of
    the key's SHA-256, written with the letters a–p instead of 0–f."""
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(char, 16)) for char in digest)


def test_only_the_permissions_the_spec_allows():
    assert MANIFEST["manifest_version"] == 3
    assert sorted(MANIFEST["permissions"]) == ["activeTab", "scripting", "storage"]
    # The only site it may call is Unfurl; job sites are reached through activeTab.
    assert MANIFEST["host_permissions"] == ["http://localhost:8000/*"]
    assert "optional_permissions" not in MANIFEST
    assert "optional_host_permissions" not in MANIFEST


def test_nothing_runs_without_a_click():
    # No content scripts on every page and no background worker in M6a (§7.2, D-139).
    assert "content_scripts" not in MANIFEST
    assert "background" not in MANIFEST
    assert MANIFEST["action"]["default_popup"] == "popup.html"


def test_the_key_gives_the_id_in_env_example():
    # A fixed key keeps the id stable when loaded unpacked; .env.example names it.
    env_example = (Path(__file__).parent.parent / ".env.example").read_text()
    assert f"chrome-extension://{extension_id(MANIFEST['key'])}" in env_example


def test_the_files_it_names_exist():
    named = [MANIFEST["action"]["default_popup"], MANIFEST["action"]["default_icon"]]
    named += MANIFEST["icons"].values()
    for path in named:
        assert (EXTENSION / path).is_file(), path


def test_the_app_address_is_the_same_in_config_and_manifest():
    config = (EXTENSION / "config.js").read_text()
    base = MANIFEST["host_permissions"][0].removesuffix("/*")
    assert f'APP_BASE_URL = "{base}"' in config


def test_the_popup_never_uses_inner_html_or_sends_cookies():
    # Page and server text goes in with textContent/.value only (D-139).
    for path in EXTENSION.glob("*.js"):
        source = path.read_text()
        assert "innerHTML" not in source, path.name
        assert "outerHTML" not in source, path.name
        assert "insertAdjacentHTML" not in source, path.name
        assert 'credentials: "include"' not in source, path.name
    assert 'credentials: "omit"' in (EXTENSION / "popup.js").read_text()
