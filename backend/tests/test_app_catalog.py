from __future__ import annotations

import pytest

from jarvis.config import AppEntry, JarvisConfig, LaunchSpec
from jarvis.text import normalize_name
from jarvis.tools.app_catalog import AppCatalog, DuplicateAliasError


@pytest.fixture(scope="module")
def catalog(config: JarvisConfig) -> AppCatalog:
    return AppCatalog(config.apps)


@pytest.mark.parametrize(
    ("spoken", "expected_id"),
    [
        ("VS Code", "vscode"),
        ("vs-code", "vscode"),
        ("vscode", "vscode"),
        ("Visual Studio Code", "vscode"),
        ("open vs code", "vscode"),
        ("Chrome", "chrome"),
        ("calculator", "calculator"),
        ("calculator kholo", "calculator"),
        ("the calculator app", "calculator"),
        ("कैलकुलेटर", "calculator"),
        ("कैलकुलेटर खोलो", "calculator"),
        ("वीएस कोड", "vscode"),
        ("क्रोम", "chrome"),
        ("फ़ाइल एक्सप्लोरर", "file_explorer"),
        ("फाइल एक्सप्लोरर", "file_explorer"),  # same words without the nukta
        ("सेटिंग्स", "settings"),
    ],
)
def test_resolves_english_hindi_and_hinglish(
    catalog: AppCatalog, spoken: str, expected_id: str
) -> None:
    app = catalog.resolve(spoken)
    assert app is not None, spoken
    assert app.id == expected_id


@pytest.mark.parametrize("spoken", ["photoshop", "", "rm -rf", "open"])
def test_unknown_names_do_not_resolve(catalog: AppCatalog, spoken: str) -> None:
    assert catalog.resolve(spoken) is None


def test_duplicate_alias_across_apps_is_rejected() -> None:
    spec = LaunchSpec(kind="shell", target="a.exe")
    apps = [
        AppEntry(id="a", display_name="A", aliases=("editor",), launch=spec),
        AppEntry(id="b", display_name="B", aliases=("Editor",), launch=spec),
    ]
    with pytest.raises(DuplicateAliasError):
        AppCatalog(apps)


def test_normalize_name_handles_unicode_forms() -> None:
    precomposed = "फ़"  # फ़ as a single code point
    decomposed = "फ़"  # फ + nukta
    assert normalize_name(precomposed) == normalize_name(decomposed) == "फ"
    assert normalize_name("  C++  ") == "c++"
