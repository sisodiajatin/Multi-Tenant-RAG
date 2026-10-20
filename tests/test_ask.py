"""/ask only ever sends authorized chunks to Claude, and maps citations back. Run: python -m tests.test_ask
Uses a stub LLM client, so no API key or spend is needed."""
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

import app.answer
from app.main import app as api
from tests.test_ingestion import token
from tests.test_isolation import seed
from tests.test_search import SECRETS

sent = []  # every request body the stub LLM received


class StubMessages:
    def create(self, **kw):
        sent.append(kw)
        docs = [b for b in kw["messages"][0]["content"] if b["type"] == "document"]
        cit = NS(document_index=0, cited_text=docs[0]["source"]["data"][:20])
        return NS(stop_reason="end_turn", content=[NS(type="thinking"), NS(type="text", text="Answer.", citations=[cit])])


app.answer._client = lambda: NS(beta=NS(messages=StubMessages()))


def main():
    ids = seed()
    client = TestClient(api)

    def hdr(tenant, roles):
        tid, uid = ids[tenant]
        return {"Authorization": f"Bearer {token(tid, uid, roles)}"}

    for (tenant, role), text in SECRETS.items():
        assert client.post("/documents", json={"title": f"{tenant}-{role}", "text": text, "allowed_roles": [role]},
                           headers=hdr(tenant, [role])).status_code == 201

    # globex engineer asks about everything; Claude must only ever see globex engineer text.
    for text in SECRETS.values():
        r = client.post("/ask", json={"question": text}, headers=hdr("globex", ["engineer"]))
        assert r.status_code == 200, r.text
    allowed = SECRETS[("globex", "engineer")]
    for req in sent:
        docs = [b["source"]["data"] for b in req["messages"][0]["content"] if b["type"] == "document"]
        assert docs == [allowed], f"LLM received unauthorized context: {docs}"

    # Response shape: answer text + citation mapped back to the real chunk.
    body = r.json()
    assert body["answer"] == "Answer." and body["refused"] is False
    assert body["citations"][0]["title"] == "globex-engineer"
    assert allowed.startswith(body["citations"][0]["cited_text"])

    # Nothing accessible => Claude is never called.
    before = len(sent)
    r = client.post("/ask", json={"question": "salary bands"}, headers=hdr("acme", ["intern"]))
    assert r.status_code == 200 and r.json()["citations"] == [] and len(sent) == before

    assert client.post("/ask", json={"question": "x"}).status_code == 401
    print(f"ask checks passed ({len(sent)} LLM calls, only authorized context sent)")


if __name__ == "__main__":
    main()
