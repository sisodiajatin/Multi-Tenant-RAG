"""Proves tenant + role isolation is enforced by the database. Run: python -m tests.test_isolation"""
import os

import psycopg

from app.db import scoped

ADMIN_DSN = os.getenv("ADMIN_DSN", "postgresql://postgres:postgres@localhost:5433/postgres")


def seed():
    with psycopg.connect(ADMIN_DSN) as c:  # superuser bypasses RLS
        c.execute("TRUNCATE tenants, users, documents, chunks, audit_log CASCADE")
        ids = {}
        for t in ("acme", "globex"):
            tid = c.execute("INSERT INTO tenants (name) VALUES (%s) RETURNING id", (t,)).fetchone()[0]
            uid = c.execute("INSERT INTO users (tenant_id, email, roles) VALUES (%s, %s, '{engineer}') RETURNING id",
                            (tid, f"dev@{t}.com")).fetchone()[0]
            for title, roles in ((f"{t} eng doc", ["engineer"]), (f"{t} hr doc", ["hr"])):
                did = c.execute("INSERT INTO documents (tenant_id, title, allowed_roles) VALUES (%s,%s,%s) RETURNING id",
                                (tid, title, roles)).fetchone()[0]
                c.execute("INSERT INTO chunks (document_id, tenant_id, allowed_roles, content) VALUES (%s,%s,%s,%s)",
                          (did, tid, roles, f"{title} chunk"))
            ids[t] = (tid, uid)
        return ids


def contents(tenant, roles):
    with scoped(tenant, roles) as c:
        return {r[0] for r in c.execute("SELECT content FROM chunks")}


def denied(fn):
    try:
        fn()
    except psycopg.Error:
        return True
    return False


def main():
    ids = seed()
    acme, globex = ids["acme"][0], ids["globex"][0]

    assert contents(acme, ["engineer"]) == {"acme eng doc chunk"}
    assert contents(acme, ["engineer", "hr"]) == {"acme eng doc chunk", "acme hr doc chunk"}
    assert contents(globex, ["hr"]) == {"globex hr doc chunk"}
    assert contents(acme, []) == set()

    # No tenant set on the connection => sees nothing (fail closed).
    with psycopg.connect(os.getenv("APP_DSN", "postgresql://app:app@localhost:5433/postgres")) as c:
        assert c.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0

    # Cannot write a row into another tenant.
    def cross_insert():
        with scoped(acme, ["engineer"]) as c:
            c.execute("INSERT INTO documents (tenant_id, title, allowed_roles) VALUES (%s, 'x', '{engineer}')", (globex,))
    assert denied(cross_insert)

    # Audit log: insert allowed, update/delete denied, other tenant's rows invisible.
    with scoped(acme, ["engineer"]) as c:
        c.execute("INSERT INTO audit_log (tenant_id, user_id, query, chunk_ids) VALUES (%s, %s, 'q', '{}')",
                  (acme, ids["acme"][1]))
    for sql in ("UPDATE audit_log SET query = 'tampered'", "DELETE FROM audit_log"):
        def tamper(sql=sql):
            with scoped(acme, ["engineer"]) as c:
                c.execute(sql)
        assert denied(tamper), sql
    with scoped(globex, ["engineer"]) as c:
        assert c.execute("SELECT count(*) FROM audit_log").fetchone()[0] == 0

    print("isolation checks passed")


if __name__ == "__main__":
    main()
