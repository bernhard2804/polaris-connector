import unittest
from pathlib import Path

from conftest_helpers import TempHome

from polaris.matching import match_project, normalize_name


class TestMatching(unittest.TestCase):
    def test_exact_match(self):
        with TempHome() as h:
            root = h.make_project_root("Musterfirma", "Falke")
            result = match_project("Musterfirma", [root])
            self.assertEqual(result.method, "exact")
            self.assertEqual(result.path, root / "Musterfirma")

    def test_normalized_match_numbering_prefix(self):
        with TempHome() as h:
            root = h.make_project_root("01_Musterfirma")
            result = match_project("Musterfirma", [root])
            self.assertEqual(result.method, "exact-normalized")
            self.assertEqual(result.path, root / "01_Musterfirma")

    def test_normalized_match_case_and_separators(self):
        with TempHome() as h:
            root = h.make_project_root("local-ai")
            result = match_project("Local AI", [root])
            self.assertEqual(result.method, "exact-normalized")

    def test_ambiguous_normalized_match_is_none(self):
        with TempHome() as h:
            # Zwei Ordner, die beide auf denselben normalisierten Namen
            # abbilden, aber keiner davon ist ein case-sensitive exaktes
            # Match fuer die Query - erzwingt den Ambiguitaets-Fall in
            # Schritt B (normalisiert).
            root = h.make_project_root("MUSTERFIRMA", "musterfirma")
            result = match_project("Musterfirma", [root])
            self.assertIsNone(result.path)
            self.assertEqual(result.method, "none")

    def test_fuzzy_match_confident(self):
        with TempHome() as h:
            root = h.make_project_root("Musterfirma")
            # Leichter Tippfehler/Variante, aber eindeutig einziger Kandidat.
            result = match_project("Musterfirm", [root])
            self.assertEqual(result.method, "fuzzy")
            self.assertEqual(result.path, root / "Musterfirma")

    def test_fuzzy_match_ambiguous_is_pending(self):
        with TempHome() as h:
            root = h.make_project_root("Alpha Project", "Alpha Projekt")
            result = match_project("Alpha Projec", [root])
            self.assertIsNone(result.path)
            self.assertEqual(result.method, "none")

    def test_no_candidates(self):
        with TempHome() as h:
            root = h.tmp / "empty"
            root.mkdir()
            result = match_project("Whatever", [root])
            self.assertIsNone(result.path)

    def test_normalize_name(self):
        self.assertEqual(normalize_name("01_Musterfirma"), "musterfirma")
        self.assertEqual(normalize_name("Local-AI"), "local ai")
        self.assertEqual(normalize_name("  Foo   Bar  "), "foo bar")


if __name__ == "__main__":
    unittest.main()
