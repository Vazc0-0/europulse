"""Ingestion layer: downloads Eurostat datasets and stores them as immutable bronze Parquet.

Nothing here knows about dbt, the warehouse or the API. The only contract with the rest of
the project is the file layout described in ``docs/contracts.md`` (sections 1 and 2).
"""

__version__ = "0.1.0"
