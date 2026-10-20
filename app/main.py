import psycopg
from fastapi import FastAPI

from app.db import APP_DSN

app = FastAPI(title="Multi-tenant RAG")


@app.get("/health")
def health():
    with psycopg.connect(APP_DSN) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
