import psycopg
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.auth import Principal, principal
from app.db import APP_DSN, scoped
from app.ingest import store

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
