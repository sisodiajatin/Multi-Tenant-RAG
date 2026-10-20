"""Search never returns chunks outside the caller's tenant + roles. Run: python -m tests.test_search"""
import psycopg
from fastapi.testclient import TestClient

from app.main import app
from tests.test_ingestion import token
from tests.test_isolation import ADMIN_DSN, seed

SECRETS = {
    ("acme", "engineer"): "Project Falcon deploy key rotation happens every Tuesday at 0300 UTC.",
    ("acme", "hr"): "Acme salary bands: staff engineer 210k to 260k, principal 280k to 340k.",
    ("globex", "engineer"): "Globex payment gateway runs on Kubernetes with blue-green deploys.",
    ("globex", "hr"): "Globex parental leave policy grants 20 weeks fully paid.",
}


def main():
    ids = seed()
    client = TestClient(app)

    def hdr(tenant, roles):
        tid, uid = ids[tenant]
        return {"Authorization": f"Bearer {token(tid, uid, roles)}"}

    for (tenant, role), text in SECRETS.items():
        r = client.post("/documents", json={"title": f"{tenant}-{role}", "text": text, "allowed_roles": [role]},
                        headers=hdr(tenant, [role]))
        assert r.status_code == 201, r.text

    # Ground truth from the superuser connection: which tenant + roles each chunk really has.
    with psycopg.connect(ADMIN_DSN) as c:
        owner = {str(i): (str(t), set(r)) for i, t, r in c.execute("SELECT id, tenant_id, allowed_roles FROM chunks")}

    def results(tenant, roles, query):
        r = client.post("/search", json={"query": query, "k": 50}, headers=hdr(tenant, roles))
        assert r.status_code == 200, r.text
        return r.json()["results"]

    # Every caller searches for every secret verbatim; nothing outside their scope may come back.
    checked = 0
    for tenant in ("acme", "globex"):
        for roles in (["engineer"], ["hr"], ["engineer", "hr"], ["intern"]):
            for text in SECRETS.values():
                for hit in results(tenant, roles, text):
                    t, allowed = owner[hit["chunk_id"]]
                    assert t == str(ids[tenant][0]), f"cross-tenant leak: {tenant} saw {hit}"
                    assert allowed & set(roles), f"role leak: {roles} saw {hit}"
                    checked += 1

    # Sanity: authorized users do find their own docs, ranked first for an exact query.
    assert results("acme", ["hr"], SECRETS[("acme", "hr")])[0]["title"] == "acme-hr"
    assert results("globex", ["engineer"], "payment gateway Kubernetes")[0]["title"] == "globex-engineer"
    assert results("acme", ["intern"], "salary bands") == []

    # Input validation and auth.
    assert client.post("/search", json={"query": "x", "k": 500}, headers=hdr("acme", ["hr"])).status_code == 422
    assert client.post("/search", json={"query": "x"}).status_code == 401

    print(f"search checks passed ({checked} returned chunks verified, 0 leaks)")


if __name__ == "__main__":
    main()
