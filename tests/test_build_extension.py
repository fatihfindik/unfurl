"""scripts/build_extension.py: a production extension only talks https (#64, D-199)."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path("scripts/build_extension.py")
spec = importlib.util.spec_from_file_location("build_extension", SCRIPT)
build_extension = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_extension)


@pytest.mark.parametrize(
    "url",
    [
        "http://unfurl.example.com",
        "unfurl.example.com",
        "https://localhost:8000",
        "https://127.0.0.1:8100",
        "https://app.localhost",
        "https://unfurl.example.com/app",
        "https://unfurl.example.com/?x=1",
        "ftp://unfurl.example.com",
    ],
)
def test_refuses_an_address_that_is_not_a_public_https_site(tmp_path, url):
    out = tmp_path / "ext"
    with pytest.raises(build_extension.BuildError):
        build_extension.build(url, out)
    assert not out.exists()  # nothing half built


def test_main_says_why_and_fails(tmp_path, capsys):
    code = build_extension.main(["http://unfurl.example.com", "--out", str(tmp_path / "x")])

    assert code == 1
    assert "https://" in capsys.readouterr().err


def test_builds_with_only_the_deployed_address(tmp_path):
    out = build_extension.build("https://unfurl.example.com/", tmp_path / "ext")

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["host_permissions"] == ["https://unfurl.example.com/*"]
    assert "key" not in manifest  # the Web Store gives the id (D-137)
    config = (out / "config.js").read_text()
    assert 'export const APP_BASE_URL = "https://unfurl.example.com";' in config
    assert "127.0.0.1" not in config.split("export const")[1]
    assert (out / "popup.js").is_file() and not (out / "TESTING.md").exists()


def test_the_source_folder_is_not_changed(tmp_path):
    before = Path("extension/manifest.json").read_text(), Path("extension/config.js").read_text()

    build_extension.build("https://unfurl.example.com", tmp_path / "ext")

    assert (
        Path("extension/manifest.json").read_text(),
        Path("extension/config.js").read_text(),
    ) == before


def test_a_second_build_replaces_the_first(tmp_path):
    out = tmp_path / "ext"
    build_extension.build("https://one.example.com", out)
    (out / "stale.txt").write_text("old")

    build_extension.build("https://two.example.com", out)

    assert not (out / "stale.txt").exists()
    assert json.loads((out / "manifest.json").read_text())["host_permissions"] == [
        "https://two.example.com/*"
    ]
