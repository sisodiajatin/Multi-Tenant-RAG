import os
from contextlib import contextmanager

import psycopg

APP_DSN = os.getenv("APP_DSN", "postgresql://app:app@localhost:5433/postgres")


@contextmanager
def scoped(tenant_id: str, roles: list[str]):
    """Transaction that can only see rows for this tenant + roles (enforced by Postgres RLS)."""
    if any("," in r for r in roles):
        raise ValueError("role names cannot contain ','")
    with psycopg.connect(APP_DSN) as conn, conn.transaction():
        # set_config(..., true) = SET LOCAL: cleared at transaction end, never leaks to the next user.
        conn.execute("SELECT set_config('app.tenant_id', %s, true), set_config('app.roles', %s, true)",
                     (str(tenant_id), ",".join(roles)))
        yield conn
