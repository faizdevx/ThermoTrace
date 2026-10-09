"""Test configuration.

* ``pytest`` (default)                 -> DATA_MODE=synthetic, no database, labelled demo data only.
* ``DATA_MODE=postgis DATABASE_URL=...`` -> tests/postgis run against a real PostGIS test database
  seeded with controlled fixtures (never the operational dataset).
"""
import os

os.environ.setdefault("DATA_MODE", "synthetic")
