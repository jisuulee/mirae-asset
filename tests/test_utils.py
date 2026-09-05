from __future__ import annotations

import unicodedata
import unittest

from disclosure_preprocess.utils import (
    nfc_equal,
    normalize_nfc,
    normalize_relative_posix_path,
    normalized_path_key,
)


class UnicodeUtilityTests(unittest.TestCase):
    def test_normalize_nfc_makes_korean_names_comparable(self) -> None:
        composed = "미래에셋증권"
        decomposed = unicodedata.normalize("NFD", composed)
        self.assertNotEqual(decomposed, composed)
        self.assertEqual(normalize_nfc(decomposed), composed)
        self.assertTrue(nfc_equal(decomposed, composed))

    def test_normalized_path_key_preserves_path_structure(self) -> None:
        composed = "raw/미래에셋/공시.xml"
        decomposed = unicodedata.normalize("NFD", composed)
        self.assertEqual(normalized_path_key(decomposed), composed)

    def test_relative_path_validation_does_not_alias_distinct_names(self) -> None:
        self.assertEqual(normalize_relative_posix_path("./raw/x.xml"), "raw/x.xml")
        self.assertEqual(normalize_relative_posix_path(".hidden"), ".hidden")
        for unsafe in ("../raw/x.xml", "/raw/x.xml", "C:/raw/x.xml"):
            with self.subTest(path=unsafe):
                with self.assertRaises(ValueError):
                    normalize_relative_posix_path(unsafe)


if __name__ == "__main__":
    unittest.main()
