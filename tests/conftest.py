"""Shared fixtures. ABC fixtures are session-scoped because reading the workbook twice
with openpyxl is the slowest thing in the suite."""

import sys
from pathlib import Path

import pytest

from finance_engine.config import ClientConfig, Settings
from finance_engine.metrics import build_metrics
from finance_engine.sources.excel import ExcelSource

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ABC_PERIOD = "2026-06"


@pytest.fixture(scope="session", autouse=True)
def generated_test_workbooks():
    """Write any missing workbook that scripts/make_test_workbooks.py generates.

    data/clients/ is git-ignored, so on a fresh clone the test_clean, test_errors and
    test_multi workbooks do not exist and the tests and golden cases that read them would
    skip. Existing files are never touched.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import make_test_workbooks
    return make_test_workbooks.ensure(PROJECT_ROOT)


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def abc_config() -> ClientConfig:
    return ClientConfig.load("abc_inc", Settings.from_env(PROJECT_ROOT))


@pytest.fixture(scope="session")
def abc_workbook(abc_config) -> Path:
    path = (PROJECT_ROOT / "data" / "clients" / "abc_inc" / "source"
            / abc_config.source_filename)
    if not path.exists():
        pytest.skip(f"ABC workbook not present at {path}")
    return path


@pytest.fixture(scope="session")
def abc_dataset(abc_config, abc_workbook):
    return ExcelSource(abc_workbook).load(abc_config)


@pytest.fixture(scope="session")
def abc_metrics(abc_dataset, abc_config) -> dict:
    return build_metrics(abc_dataset, abc_config, ABC_PERIOD)


@pytest.fixture
def abc_metric_map(abc_metrics) -> dict:
    return {m["name"]: m for m in abc_metrics["metrics"]}
