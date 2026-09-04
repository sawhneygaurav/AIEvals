"""Focused tests for the dependency-free offline fixture."""

import importlib.util
import sys
import unittest
from pathlib import Path

# Load this one dependency-free module directly.  The wider application may have
# optional packages installed, but they should not be needed to verify the fixture.
MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "competitive_scoring" / "demo_data.py"
SPEC = importlib.util.spec_from_file_location("demo_data_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - defensive setup guard
    raise RuntimeError(f"Could not load demo fixture from {MODULE_PATH}")
DEMO_DATA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DEMO_DATA
SPEC.loader.exec_module(DEMO_DATA)

DEMO_TICKERS = DEMO_DATA.DEMO_TICKERS
DISCLAIMER = DEMO_DATA.DISCLAIMER
get_demo_companies = DEMO_DATA.get_demo_companies
get_demo_company = DEMO_DATA.get_demo_company
get_demo_dataset = DEMO_DATA.get_demo_dataset
get_demo_sources = DEMO_DATA.get_demo_sources


class DemoDataTests(unittest.TestCase):
    def test_fixture_is_explicitly_illustrative_and_has_expected_universe(self) -> None:
        dataset = get_demo_dataset()

        self.assertIs(dataset["metadata"]["is_illustrative"], True)
        self.assertIs(dataset["metadata"]["not_for_investment_decisions"], True)
        self.assertIsNone(dataset["metadata"]["live_as_of"])
        self.assertIn("ILLUSTRATIVE OFFLINE FIXTURE", dataset["metadata"]["disclaimer"])
        self.assertEqual(tuple(dataset["companies"]), DEMO_TICKERS)

        for company in dataset["companies"].values():
            self.assertEqual(company["data_status"], "illustrative_offline_fixture")
            self.assertEqual(company["disclaimer"], DISCLAIMER)
            self.assertIs(company["confidence"]["real_world_accuracy_asserted"], False)

    def test_every_company_has_scoring_sections_and_valid_demo_technicals(
        self,
    ) -> None:
        required_sections = {
            "business",
            "fundamentals",
            "growth",
            "management",
            "valuation",
            "technicals",
            "freshness",
            "confidence",
        }

        for company in get_demo_companies().values():
            self.assertTrue(required_sections.issubset(company))
            self.assertTrue(company["fundamentals"]["period_label"].startswith("ILLUSTRATIVE_"))
            self.assertTrue(company["growth"]["period_label"].startswith("ILLUSTRATIVE_"))
            self.assertTrue(company["valuation"]["period_label"].startswith("ILLUSTRATIVE_"))

            technicals = company["technicals"]
            self.assertGreater(technicals["close"], 0)
            self.assertGreater(technicals["sma_20"], 0)
            self.assertGreater(technicals["sma_50"], 0)
            self.assertGreaterEqual(technicals["rsi_14"], 0)
            self.assertLessEqual(technicals["rsi_14"], 100)

    def test_sources_resolve_without_claiming_to_support_demo_numbers(self) -> None:
        companies = get_demo_companies()
        sources = get_demo_sources()

        for company in companies.values():
            self.assertTrue(company["reference_source_ids"])
            for source_id in company["reference_source_ids"]:
                source = sources[source_id]
                self.assertEqual(source["ticker"], company["ticker"])
                self.assertTrue(source["url"].startswith("https://"))
                self.assertIs(source["supports_fixture_numbers"], False)
                self.assertIn("Reference URL only", source["fixture_note"])

    def test_public_helpers_return_defensive_copies(self) -> None:
        first = get_demo_dataset()
        second = get_demo_dataset()
        self.assertEqual(first, second)

        first["companies"]["PNGJL"]["fundamentals"]["roe_pct"] = 999.0
        first["metadata"]["ticker_order"].append("FAKE")

        fresh = get_demo_dataset()
        self.assertEqual(fresh, second)
        self.assertNotEqual(fresh["companies"]["PNGJL"]["fundamentals"]["roe_pct"], 999.0)
        self.assertNotIn("FAKE", fresh["metadata"]["ticker_order"])

    def test_single_company_lookup_is_friendly_and_safe(self) -> None:
        company = get_demo_company("  pngjl ")
        self.assertEqual(company["ticker"], "PNGJL")
        self.assertIs(company["is_target"], True)

        company["valuation"]["pe_ttm"] = -1
        self.assertGreater(get_demo_company("PNGJL")["valuation"]["pe_ttm"], 0)

        with self.assertRaisesRegex(KeyError, "Unknown demo ticker"):
            get_demo_company("NOT_A_TICKER")

        with self.assertRaisesRegex(TypeError, "ticker must be a string"):
            get_demo_company(123)  # type: ignore[arg-type]

    def test_reporting_basis_caveat_is_machine_readable(self) -> None:
        companies = get_demo_companies()

        for ticker in ("PNGJL", "KALYANKJIL", "SENCO"):
            self.assertEqual(
                companies[ticker]["fundamentals"]["reporting_basis"],
                "consolidated_demo_basis",
            )
        self.assertEqual(
            companies["THANGAMAYL"]["fundamentals"]["reporting_basis"],
            "standalone_demo_basis",
        )


if __name__ == "__main__":
    unittest.main()
