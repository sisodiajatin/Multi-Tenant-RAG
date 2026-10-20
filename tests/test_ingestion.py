"""Ingestion through the API respects tenant + role rules. Run: python -m tests.test_ingestion"""
import os
import time

os.environ.setdefault("JWT_SECRET", "test-secret-only-for-local-testing-0123456789")  # dev/test only

import jwt
from fastapi.testclient import TestClient

from app.db import scoped
from app.ingest import chunk
from app.main import app
from tests.test_isolation import seed


def token(tenant_id, user_id, roles):
    return jwt.encode({"sub": str(user_id), "tenant_id": str(tenant_id), "roles": roles,
                       "exp": int(time.time()) + 300}, os.environ["JWT_SECRET"], algorithm="HS256")


def main():
    # Chunker: overlapping windows, nothing dropped, short text = one chunk.
    words = [f"w{i}" for i in range(400)]
    parts = chunk(" ".join(words), size=150, overlap=30)
    assert parts[0].split()[-30:] == parts[1].split()[:30]
    assert parts[-1].split()[-1] == "w399"
    assert chunk("tiny doc") == ["tiny doc"]

    ids = seed()
    (acme, acme_user), (globex, globex_user) = ids["acme"], ids["globex"]
    client = TestClient(app)
    auth = {"Authorization": f"Bearer {token(acme, acme_user, ['engineer'])}"}
    body = {"title": "runbook", "text": " ".join(["deploy the service"] * 200), "allowed_roles": ["engineer"]}

    r = client.post("/documents", json=body, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["chunks"] > 1

    # Chunks landed in acme with embeddings, invisible to globex even with the same role.
    with scoped(acme, ["engineer"]) as c:
        n, missing = c.execute("SELECT count(*), count(*) FILTER (WHERE embedding IS NULL) FROM chunks"
                               " WHERE document_id = %s", (r.json()["document_id"],)).fetchone()
        assert (n, missing) == (r.json()["chunks"], 0)
    with scoped(globex, ["engineer"]) as c:
        assert c.execute("SELECT count(*) FROM chunks WHERE document_id = %s",
                         (r.json()["document_id"],)).fetchone()[0] == 0

    # Granting a role you don't hold is refused by the database policy.
    r = client.post("/documents", json={**body, "allowed_roles": ["hr"]}, headers=auth)
    assert r.status_code == 403, r.text

    # Bad or missing tokens.
    assert client.post("/documents", json=body).status_code == 401
    assert client.post("/documents", json=body, headers={"Authorization": "Bearer junk"}).status_code == 401
    forged = jwt.encode({"sub": str(acme_user), "tenant_id": str(globex), "roles": ["engineer"],
                         "exp": int(time.time()) + 300}, "wrong-secret-also-for-local-testing-0123456789", algorithm="HS256")
    assert client.post("/documents", json=body, headers={"Authorization": f"Bearer {forged}"}).status_code == 401

    print("ingestion checks passed")


if __name__ == "__main__":
    main()
