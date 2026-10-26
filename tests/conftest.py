"""Test setup.

The API tests need TEST_DATABASE_URL: an empty, throwaway database and an admin user.
They drop and recreate the catalog and app schemas there, then call the API as app_user,
so the real grants are tested too. The catalog schema is read from the pipeline repo
(a file read, not a code import): ../the-catalogue-pipeline/sql/catalog_schema.sql,
or set CATALOG_SCHEMA_PATH.
"""

import os
from pathlib import Path

from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
CATALOG_SCHEMA_PATH = Path(os.environ.get(
    "CATALOG_SCHEMA_PATH", ROOT.parent / "the-catalogue-pipeline" / "sql" / "catalog_schema.sql"
))

# app.main reads its settings at import time, so set them before any test imports it.
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = os.environ.get("TEST_APP_DATABASE_URL") or make_conninfo(
        TEST_DATABASE_URL, user="app_user"
    )
else:
    os.environ.setdefault("DATABASE_URL", "postgresql://unused@localhost/unused")
os.environ["CORS_ORIGINS"] = "https://the-catalogue.vercel.app"
