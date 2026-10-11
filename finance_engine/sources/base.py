"""The `DataSource` boundary.

Everything upstream of the engine sits behind this interface. Excel implements it now;
a QuickBooks source can implement it later without any change to validation, metrics,
reconciliation, commentary or reporting — they all consume a `Dataset`.

A source's only job is to produce a faithful, traceable `Dataset`. It must not calculate
derived figures, repair data, or substitute zero for missing values.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from finance_engine.config import ClientConfig
from finance_engine.model import Dataset


class DataSource(ABC):
    """Reads one client's financial statements into a `Dataset`."""

    #: Short identifier recorded in metrics.json provenance, e.g. "excel".
    source_type: str = "unknown"

    @abstractmethod
    def load(self, config: ClientConfig) -> Dataset:
        """Read everything available for this client.

        Raises:
            LayoutError: the source could not be safely interpreted against the config.
                Raise this rather than guessing at an ambiguous layout.
        """

    @abstractmethod
    def describe(self) -> dict:
        """Provenance for metrics.json — path, checksum, and similar. No secrets."""
