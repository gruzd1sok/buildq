"""Localization coverage of the menu bar app and version consistency across the package files."""
import json
import re
import unittest

from helpers import ROOT, load_cli

LANGS = ("en", "ru")
SPEC = re.compile(r"%(?:lld|@|d)")


def swift_keys():
    src = "\n".join(p.read_text() for p in (ROOT / "app" / "Sources").glob("*.swift"))
    keys = re.findall(r'\bL\("((?:[^"\\]|\\.)*)"', src)
    return {k.replace("\\n", "\n").replace('\\"', '"') for k in keys}


def translations(entry, lang):
    loc = entry.get("localizations", {}).get(lang)
    if not loc:
        return []
    if "stringUnit" in loc:
        return [loc["stringUnit"]["value"]]
    return [v["stringUnit"]["value"] for v in loc["variations"]["plural"].values()]


class Localization(unittest.TestCase):
    catalog = json.loads((ROOT / "app" / "Localizable.xcstrings").read_text())["strings"]

    def test_every_ui_string_is_in_the_catalog(self):
        self.assertEqual(sorted(swift_keys() - self.catalog.keys()), [], "missing from Localizable.xcstrings")

    def test_catalog_has_no_unused_keys(self):
        self.assertEqual(sorted(self.catalog.keys() - swift_keys()), [], "unused in app/Sources")

    def test_every_key_is_translated(self):
        for key, entry in self.catalog.items():
            for lang in LANGS:
                values = translations(entry, lang) or ([key] if lang == "en" else [])
                with self.subTest(key=key, lang=lang):
                    self.assertTrue(values, "no translation")
                    for v in values:
                        self.assertEqual(SPEC.findall(v), SPEC.findall(key), f"format specifiers differ: {v!r}")

    def test_russian_plurals_have_all_forms(self):
        for key, entry in self.catalog.items():
            plural = entry["localizations"].get("ru", {}).get("variations", {}).get("plural")
            if plural:
                self.assertEqual(set(plural), {"one", "few", "many", "other"}, key)


class Versions(unittest.TestCase):
    def test_versions_match(self):
        version = load_cli().__version__
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(plugin["version"], version)
        self.assertIn(f"## [{version}]", (ROOT / "CHANGELOG.md").read_text())
        self.assertIn(f"/v{version}.tar.gz", (ROOT / "Formula" / "buildq.rb").read_text())


if __name__ == "__main__":
    unittest.main()
