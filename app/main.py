import anthropic
import psycopg
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.answer import answer
from app.auth import Principal, principal
from app.db import APP_DSN, scoped
from app.ingest import embed, store

app = FastAPI(title="Multi-tenant RAG")


@app.get("/health")
def health():
    with psycopg.connect(APP_DSN) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


class DocumentIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=2_000_000)
    allowed_roles: list[str] = Field(min_length=1)


@app.post("/documents", status_code=201)
def create_document(doc: DocumentIn, user: Principal = Depends(principal)):
    # Tenant always comes from the token; the RLS policy rejects granting roles the user doesn't hold.
    try:
        with scoped(user.tenant_id, user.roles) as conn:
            doc_id, n = store(conn, user.tenant_id, doc.title, doc.text, doc.allowed_roles)
    except psycopg.errors.InsufficientPrivilege:
        raise HTTPException(403, "allowed_roles must be a subset of your roles")
    return {"document_id": doc_id, "chunks": n}


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=50)


def retrieve(user: Principal, query: str, k: int) -> list[dict]:
    # No tenant/role WHERE clause on purpose: RLS filters every row before it can be ranked or returned.
    with scoped(user.tenant_id, user.roles) as conn:
        # Keep scanning the HNSW index until k rows survive the RLS filter (pgvector >= 0.8).
        conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
        rows = conn.execute(
            "SELECT c.id, c.document_id, d.title, c.content, 1 - (c.embedding <=> %(q)s::vector)"
            " FROM chunks c JOIN documents d ON d.id = c.document_id"
            " WHERE c.embedding IS NOT NULL"
            " ORDER BY c.embedding <=> %(q)s::vector LIMIT %(k)s",
            {"q": embed([query])[0], "k": k}).fetchall()
    return [{"chunk_id": str(i), "document_id": str(d), "title": t, "content": c, "score": round(s, 4)}
            for i, d, t, c, s in rows]


@app.post("/search")
def search(body: SearchIn, user: Principal = Depends(principal)):
    return {"results": retrieve(user, body.query, body.k)}


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=20)


@app.post("/ask")
def ask(body: AskIn, user: Principal = Depends(principal)):
    # Claude only ever receives chunks that already passed the RLS filter for this user.
    chunks = retrieve(user, body.question, body.k)
    if not chunks:
        return {"answer": "No documents you have access to match this question.", "citations": [], "refused": False}
    try:
        return answer(body.question, chunks)
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"LLM error {e.status_code}")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "LLM unreachable")
