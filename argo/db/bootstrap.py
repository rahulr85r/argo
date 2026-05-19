from pathlib import Path

from argo.db import get_conn
from argo.db.seed import seed_if_empty

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_db() -> None:
    schema_sql = SCHEMA_PATH.read_text()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()
    seed_if_empty()
