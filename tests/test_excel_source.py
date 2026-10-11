"""The reader must refuse ambiguity rather than guess.

Every test here asserts that a misleading layout produces a `LayoutError` naming the
problem. A reader that quietly picked one interpretation would produce a report that looks
correct and is not, which is the worst possible failure for this system.
"""

from __future__ import annotations

import pytest
from openpyxl import load_workbook

from finance_engine.config import ClientConfig, ConfigError
from finance_engine.model import LayoutError
from finance_engine.sources.excel import ExcelSource, _coerce, _parse_period
from tests.helpers import build_small_workbook, write_small_config


def load(tmp_path, overrides=None, **workbook_kwargs):
    workbook = build_small_workbook(tmp_path / "tiny.xlsx", **workbook_kwargs)
    config = ClientConfig.load_path(write_small_config(tmp_path, overrides))
    return ExcelSource(workbook).load(config)


class TestHappyPath:
    def test_periods_are_normalised_to_oldest_first(self, tmp_path):
        dataset = load(tmp_path)
        assert dataset.period_keys() == ["2025-01", "2025-02", "2025-03"]

    def test_newest_first_is_also_normalised_to_oldest_first(self, tmp_path):
        dataset = load(
            tmp_path,
            overrides={"sheets": {"income_statement": {"date_order": "newest_first"}}},
            newest_first=True,
        )
        assert dataset.period_keys() == ["2025-01", "2025-02", "2025-03"]

    def test_labels_are_taken_from_the_first_populated_column(self, tmp_path):
        sheet = load(tmp_path).income_statement
        # "Total Income" sits in column A; "Widgets" in column B.
        assert sheet.section_total("revenue").label == "Total Income"
        assert sheet.by_label("Widgets") is not None

    def test_formula_rows_are_recognised(self, tmp_path):
        sheet = load(tmp_path).income_statement
        total = sheet.section_total("revenue")
        cell = total.cells["2025-02"]
        assert cell.is_formula
        assert "SUM" in cell.formula
        assert cell.value == 200.0

    def test_titles_above_the_header_are_captured(self, tmp_path):
        sheet = load(tmp_path).income_statement
        assert "Income Statement" in sheet.titles
        assert "Tiny Co" in sheet.titles

    def test_checksum_is_stable_and_recorded(self, tmp_path):
        workbook = build_small_workbook(tmp_path / "tiny.xlsx")
        source = ExcelSource(workbook)
        assert source.sha256() == source.sha256()
        assert len(source.sha256()) == 64
        assert source.describe()["sha256"] == source.sha256()

    def test_source_file_is_never_modified(self, tmp_path):
        workbook = build_small_workbook(tmp_path / "tiny.xlsx")
        before = workbook.read_bytes()
        config = ClientConfig.load_path(write_small_config(tmp_path))
        ExcelSource(workbook).load(config)
        assert workbook.read_bytes() == before


class TestRefusesAmbiguity:
    def test_duplicate_period_columns_are_refused(self, tmp_path):
        # The repeated column lands in F, so the config's data range has to reach it —
        # otherwise the duplicate is simply never read and there is nothing to refuse.
        with pytest.raises(LayoutError, match="duplicate period"):
            load(tmp_path,
                 overrides={"sheets": {"income_statement":
                                       {"last_data_column": "F"}}},
                 duplicate_period=True)

    def test_a_column_outside_the_configured_range_is_ignored(self, tmp_path):
        # The same workbook, with the range left at E: the fourth column is out of scope,
        # so three clean periods are read.
        dataset = load(tmp_path, duplicate_period=True)
        assert dataset.period_keys() == ["2025-01", "2025-02", "2025-03"]

    def test_declared_order_is_verified_not_trusted(self, tmp_path):
        # The workbook is oldest-first but the config claims newest-first. Trusting the
        # config would reverse every month-over-month comparison in the report.
        with pytest.raises(LayoutError, match="not strictly newest-to-oldest"):
            load(tmp_path,
                 overrides={"sheets": {"income_statement":
                                       {"date_order": "newest_first"}}})

    def test_unparseable_period_header_is_refused(self, tmp_path):
        with pytest.raises(LayoutError, match="cannot read"):
            load(tmp_path,
                 overrides={"sheets": {"income_statement":
                                       {"date_formats": ["%d/%m/%Y"]}}})

    def test_missing_sheet_names_what_it_found(self, tmp_path):
        with pytest.raises(LayoutError, match="expected a sheet named"):
            load(tmp_path,
                 overrides={"sheets": {"income_statement": {"name": "Not There"}}})

    def test_missing_section_anchor_points_at_the_config(self, tmp_path):
        with pytest.raises(LayoutError, match="Total Revenues"):
            load(tmp_path, overrides={"income_statement": {"sections": [
                {"key": "revenue", "header_label": "Income",
                 "total_label": "Total Revenues"}]}})

    def test_total_above_its_header_is_refused(self, tmp_path):
        with pytest.raises(LayoutError, match="not below"):
            load(tmp_path, overrides={"income_statement": {"sections": [
                {"key": "revenue", "header_label": "Total Income",
                 "total_label": "Income"}]}})

    def test_group_outside_its_section_is_refused(self, tmp_path):
        with pytest.raises(LayoutError, match="does not sit inside"):
            load(tmp_path, overrides={"income_statement": {"sections": [
                {"key": "revenue", "header_label": "Income",
                 "total_label": "Total Income",
                 "subtotal_groups": [{"key": "salaries", "name": "Salaries",
                                      "header_label": "Salaries",
                                      "total_label": "Total Salaries"}]}]}})

    def test_missing_workbook_is_reported(self, tmp_path):
        with pytest.raises(LayoutError, match="not found"):
            ExcelSource(tmp_path / "absent.xlsx")


class TestConfigValidation:
    def test_missing_required_key_is_reported(self, tmp_path):
        path = write_small_config(tmp_path)
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        del raw["company_name"]
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        with pytest.raises(ConfigError, match="company_name"):
            ClientConfig.load_path(path)

    def test_bad_date_order_value_is_reported(self, tmp_path):
        with pytest.raises(ConfigError, match="date_order"):
            ClientConfig.load_path(
                write_small_config(tmp_path, {"sheets": {"income_statement":
                                                         {"date_order": "sideways"}}})
            )

    def test_duplicate_section_keys_are_reported(self, tmp_path):
        with pytest.raises(ConfigError, match="duplicate section keys"):
            ClientConfig.load_path(write_small_config(tmp_path, {
                "income_statement": {"sections": [
                    {"key": "revenue", "header_label": "Income",
                     "total_label": "Total Income"},
                    {"key": "revenue", "header_label": "Income",
                     "total_label": "Total Income"},
                ]}}))

    def test_unknown_client_lists_what_exists(self, tmp_path):
        from finance_engine.config import Settings
        write_small_config(tmp_path)
        settings = Settings.from_env(tmp_path)
        object.__setattr__(settings, "config_dir", tmp_path)
        with pytest.raises(ConfigError, match="tiny_co"):
            ClientConfig.load("nobody", settings)


class TestCellCoercion:
    @pytest.mark.parametrize("raw,expected", [
        (1234.56, 1234.56),
        (0, 0.0),
        (-50, -50.0),
        ("1,234.56", 1234.56),
        ("$1,234.56", 1234.56),
        ("(1,234.56)", -1234.56),
        ("  42  ", 42.0),
    ])
    def test_readable_values(self, raw, expected):
        assert _coerce(raw) == (expected, False)

    def test_blank_stays_none_and_is_not_flagged(self):
        assert _coerce(None) == (None, False)
        assert _coerce("") == (None, False)
        assert _coerce("   ") == (None, False)

    @pytest.mark.parametrize("raw", ["n/a", "TBC", "#REF!", "#DIV/0!", True, False])
    def test_unreadable_values_are_flagged_never_zeroed(self, raw):
        value, unreadable = _coerce(raw)
        assert value is None
        assert unreadable is True

    def test_zero_and_blank_are_distinguishable(self):
        assert _coerce(0)[0] == 0.0
        assert _coerce(None)[0] is None


class TestPeriodParsing:
    @pytest.mark.parametrize("text,fmt,expected", [
        ("Jun 2026", "%b %Y", (2026, 6)),
        ("30 Jun 2026", "%d %b %Y", (2026, 6)),
        ("28 Feb 2025", "%d %b %Y", (2025, 2)),
    ])
    def test_supported_formats(self, text, fmt, expected):
        period = _parse_period(text, [fmt])
        assert (period.year, period.month) == expected

    def test_datetime_cells_are_accepted(self):
        import datetime as dt
        period = _parse_period(dt.datetime(2026, 6, 30), ["%b %Y"])
        assert (period.year, period.month) == (2026, 6)

    def test_unknown_format_returns_none_so_the_caller_can_refuse(self):
        assert _parse_period("Q2 FY26", ["%b %Y"]) is None


class TestAbcWorkbookShape:
    """Facts about the real workbook, asserted so a replaced file is noticed."""

    def test_two_sheets_with_different_label_offsets(self, abc_dataset):
        assert abc_dataset.income_statement.name == "Income Statement"
        assert abc_dataset.balance_sheet.name == "Balance Sheet"

    def test_eighteen_contiguous_months_newest_first_in_the_file(self, abc_dataset):
        keys = abc_dataset.period_keys()
        assert len(keys) == 18
        assert keys[0] == "2025-01"
        assert keys[-1] == "2026-06"

    def test_the_eight_formula_rows_are_detected(self, abc_dataset):
        sheet = abc_dataset.income_statement
        expected = {
            "Total Income", "Total Cost of Goods Sold", "Gross Profit",
            "Total S & M Payroll", "Total Marketing", "Total Operating Expenses",
            "Operating Income", "Net Income",
        }
        formula_labels = {
            item.label for item in sheet.line_items
            if any(c.is_formula for c in item.cells.values())
        }
        assert expected <= formula_labels

    def test_no_formula_value_is_unreadable(self, abc_dataset):
        sheet = abc_dataset.income_statement
        unreadable = [
            (i.label, p) for i in sheet.line_items
            for p, c in i.cells.items() if c.unreadable_formula
        ]
        assert unreadable == []

    def test_subtotal_groups_do_not_double_count(self, abc_dataset):
        sheet = abc_dataset.income_statement
        direct = sheet.direct_items("opex")
        members = (sheet.group_members("s_and_m_payroll")
                   + sheet.group_members("marketing"))
        assert len(direct) == 49
        assert len(members) == 21
        # Every leaf is either direct or a group member, never both.
        assert len(sheet.in_section("opex")) == 70
        assert {i.row for i in direct}.isdisjoint({i.row for i in members})

    def test_balance_sheet_labels_sit_in_a_different_column(self, abc_dataset):
        bs = abc_dataset.balance_sheet
        assert bs.by_label("Total Cash and Cash Equivalents") is not None
        assert bs.by_label("Deferred Revenue") is not None
        assert len(bs.periods) == 18
