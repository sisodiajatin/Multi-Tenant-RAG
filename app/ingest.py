from functools import lru_cache

from fastembed import TextEmbedding

# all-MiniLM-L6-v2 truncates at 256 word-pieces, so keep chunks around ~200 tokens.
CHUNK_WORDS = 150
OVERLAP_WORDS = 30


def chunk(text: str, size: int = CHUNK_WORDS, overlap: int = OVERLAP_WORDS) -> list[str]:
    # ponytail: word-window splitter, swap for a sentence/heading-aware one if retrieval quality suffers.
    words = text.split()
    step = size - overlap
    return [" ".join(words[i:i + size]) for i in range(0, max(len(words) - overlap, 1), step)]


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    return TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")


def embed(texts: list[str]) -> list[str]:
    """Returns pgvector literals ('[0.1,...]') ready for a %s::vector parameter."""
    return [str(v.tolist()) for v in _model().embed(texts)]


def store(conn, tenant_id: str, title: str, text: str, allowed_roles: list[str]) -> tuple[str, int]:
    """Insert a document + its chunks on a scoped() connection. Chunks inherit the document's ACL."""
    doc_id = conn.execute(
        "INSERT INTO documents (tenant_id, title, allowed_roles) VALUES (%s, %s, %s) RETURNING id",
        (tenant_id, title, allowed_roles)).fetchone()[0]
    pieces = chunk(text)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chunks (document_id, tenant_id, allowed_roles, content, embedding)"
            " VALUES (%s, %s, %s, %s, %s::vector)",
            [(doc_id, tenant_id, allowed_roles, p, e) for p, e in zip(pieces, embed(pieces))])
    return str(doc_id), len(pieces)
