from __future__ import annotations

import unicodedata
import unittest

from disclosure_preprocess.metadata_normalization import (
    MetadataValidationStatus,
    MismatchReason,
    build_canonical_metadata,
    classify_mismatch_reason,
    extract_source_observations,
    format_rcept_date,
    validation_status,
)


def _manifest_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "doc_id": "periodic_20250318000001",
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "listed_name": "삼성전자",
        "stock_code": "005930",
        "industry": "제조업",
        "sector": "반도체",
        "doc_group": "periodic",
        "doc_subtype": "annual",
        "report_nm": "사업보고서 (2024.12)",
        "is_correction": False,
        "rcept_no": "20250318000001",
        "rcept_dt": "20250318",
        "flr_nm": "삼성전자",
        "base_year": 2024,
        "base_month": 12,
    }
    record.update(overrides)
    return record


class CanonicalMetadataTests(unittest.TestCase):
    def test_identifier_strings_and_leading_zeroes_are_preserved(self) -> None:
        metadata, warnings = build_canonical_metadata(_manifest_record())

        self.assertEqual(warnings, [])
        self.assertEqual(metadata["authority"], "manifest")
        self.assertIsInstance(metadata["corp_code"], str)
        self.assertIsInstance(metadata["stock_code"], str)
        self.assertIsInstance(metadata["rcept_no"], str)
        self.assertEqual(metadata["corp_code"], "00126380")
        self.assertEqual(metadata["stock_code"], "005930")

    def test_date_formatting_is_strict_and_never_uses_rcept_no(self) -> None:
        self.assertEqual(format_rcept_date("20250318"), "2025-03-18")
        self.assertIsNone(format_rcept_date("20250230"))
        self.assertIsNone(format_rcept_date("2025-03-18"))
        self.assertIsNone(format_rcept_date(None))

        metadata, warnings = build_canonical_metadata(
            _manifest_record(rcept_dt="20250230")
        )
        self.assertIsNone(metadata["rcept_date"])
        self.assertIn("RCEPT_DATE_INVALID", {item["code"] for item in warnings})
        self.assertEqual(metadata["rcept_no"], "20250318000001")

    def test_rcept_no_prefix_difference_keeps_manifest_date(self) -> None:
        metadata, warnings = build_canonical_metadata(
            _manifest_record(
                doc_id="periodic_20250318000001",
                rcept_no="20250318000001",
                rcept_dt="20250319",
            )
        )
        self.assertEqual(metadata["rcept_dt"], "20250319")
        self.assertEqual(metadata["rcept_date"], "2025-03-19")
        self.assertIn(
            "RCEPT_DATE_PREFIX_DIFFERENCE", {item["code"] for item in warnings}
        )

    def test_company_fields_and_raw_values_remain_separate(self) -> None:
        metadata, warnings = build_canonical_metadata(
            _manifest_record(
                corp_name="엘에스일렉트릭",
                listed_name="LS ELECTRIC",
            )
        )
        self.assertEqual(warnings, [])
        self.assertEqual(metadata["corp_name"], "엘에스일렉트릭")
        self.assertEqual(metadata["listed_name"], "LS ELECTRIC")

    def test_non_string_canonical_text_is_invalid(self) -> None:
        _, warnings = build_canonical_metadata(_manifest_record(corp_name=123))
        errors = [
            item
            for item in warnings
            if item["code"] == "CANONICAL_TYPE_INVALID"
            and item["field"] == "corp_name"
        ]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["severity"], "error")

    def test_source_observations_preserve_raw_and_provenance(self) -> None:
        parsed = {
            "main_document": {
                "source_file": {
                    "relative_path": "raw/periodic/삼성전자/1/main.xml",
                    "role": "main",
                    "detected_format": "xml",
                },
                "source_metadata": {
                    "company_name": "삼성전자",
                    "company_name_raw": "삼성전자 ",
                    "company_name_provenance": {
                        "source_file": "raw/periodic/삼성전자/1/main.xml",
                        "dom_path": "/DOCUMENT/COMPANY-NAME",
                    },
                },
            }
        }
        observations = extract_source_observations(parsed)["observations"]
        self.assertEqual(len(observations), 1)
        company = observations[0]["values"]["company_name"]
        self.assertEqual(company["raw"], "삼성전자 ")
        self.assertEqual(company["normalized"], "삼성전자")
        self.assertEqual(
            company["provenance"]["dom_path"], "/DOCUMENT/COMPANY-NAME"
        )


class MismatchTaxonomyTests(unittest.TestCase):
    def test_whitespace_difference(self) -> None:
        reason, severity, normalized_equal = classify_mismatch_reason(
            field="corp_name",
            canonical_value="삼성전자",
            source_value=" 삼성전자  ",
        )
        self.assertEqual(reason, MismatchReason.WHITESPACE_DIFFERENCE)
        self.assertEqual(severity, "info")
        self.assertTrue(normalized_equal)

    def test_unicode_normalization_difference(self) -> None:
        canonical = "한글"
        source = unicodedata.normalize("NFD", canonical)
        reason, _, normalized_equal = classify_mismatch_reason(
            field="corp_name",
            canonical_value=canonical,
            source_value=source,
        )
        self.assertEqual(reason, MismatchReason.UNICODE_NORMALIZATION_DIFFERENCE)
        self.assertTrue(normalized_equal)

    def test_actual_difference_is_not_forced_equal(self) -> None:
        reason, severity, normalized_equal = classify_mismatch_reason(
            field="stock_code",
            canonical_value="005930",
            source_value="000660",
        )
        self.assertEqual(reason, MismatchReason.SOURCE_VALUE_DIFFERENT)
        self.assertEqual(severity, "warning")
        self.assertFalse(normalized_equal)

    def test_null_difference(self) -> None:
        reason, _, normalized_equal = classify_mismatch_reason(
            field="corp_name",
            canonical_value="삼성전자",
            source_value=None,
        )
        self.assertEqual(reason, MismatchReason.SOURCE_VALUE_MISSING)
        self.assertFalse(normalized_equal)

    def test_listed_name_display_form_is_informational(self) -> None:
        reason, severity, normalized_equal = classify_mismatch_reason(
            field="corp_name",
            canonical_value="NC",
            source_value="(주)엔씨소프트",
            listed_name_value="엔씨소프트",
        )
        self.assertEqual(reason, MismatchReason.DISPLAY_NAME_DIFFERENCE)
        self.assertEqual(severity, "info")
        self.assertFalse(normalized_equal)
        self.assertEqual(
            validation_status([], 1), MetadataValidationStatus.VALID_WITH_WARNING
        )

    def test_arbitrary_one_to_one_alias_is_not_exonerated(self) -> None:
        reason, severity, normalized_equal = classify_mismatch_reason(
            field="corp_name",
            canonical_value="NAVER",
            source_value="네이버(주)",
            listed_name_value="NAVER",
        )
        self.assertEqual(reason, MismatchReason.SOURCE_VALUE_DIFFERENT)
        self.assertEqual(severity, "warning")
        self.assertFalse(normalized_equal)


if __name__ == "__main__":
    unittest.main()
