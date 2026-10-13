"""Which reader opens a client's source file: a workbook or a QuickBooks snapshot."""

from __future__ import annotations

from pathlib import Path

from finance_engine.config import ClientConfig


def source_pattern(config: ClientConfig) -> str:
    """The files a client's source folder holds: workbooks, or QuickBooks snapshots."""
    return "*.json" if config.source_type == "quickbooks" else "*.xlsx"


def open_source(path: Path, config: ClientConfig):
    if config.source_type == "quickbooks":
        from finance_engine.sources.quickbooks import QuickBooksSnapshotSource
        return QuickBooksSnapshotSource(path)
    from finance_engine.sources.excel import ExcelSource
    return ExcelSource(path)
