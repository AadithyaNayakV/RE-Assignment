"""Shared fixtures. ABC fixtures are session-scoped because reading the workbook twice
with openpyxl is the slowest thing in the suite."""

from pathlib import Path

import pytest

from finance_engine.config import ClientConfig, Settings
from finance_engine.metrics import build_metrics
from finance_engine.sources.excel import ExcelSource

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ABC_PERIOD = "2026-06"


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
