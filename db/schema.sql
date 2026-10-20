-- Multi-tenant RAG schema.
-- Isolation is enforced by Postgres row-level security, not by application WHERE clauses:
-- the app connects as `app` (not table owner, not superuser), so every query is filtered
-- by the tenant + roles set on the transaction. Nothing set => nothing visible (fail closed).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE tenants (
    id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE
);

CREATE TABLE users (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    email     text NOT NULL,
    roles     text[] NOT NULL DEFAULT '{}',
    UNIQUE (tenant_id, email)
);

CREATE TABLE documents (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants(id),
    title         text NOT NULL,
    allowed_roles text[] NOT NULL CHECK (cardinality(allowed_roles) > 0),
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, tenant_id)
);

-- Chunks carry their own copy of tenant + ACL so retrieval never needs a join to filter.
-- The composite FK makes it impossible to attach a chunk to another tenant's document.
CREATE TABLE chunks (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id   uuid NOT NULL,
    tenant_id     uuid NOT NULL,
    allowed_roles text[] NOT NULL CHECK (cardinality(allowed_roles) > 0),
    content       text NOT NULL,
    embedding     vector(384),  -- all-MiniLM-L6-v2
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents(id, tenant_id) ON DELETE CASCADE
);
CREATE INDEX ON chunks (tenant_id);
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE audit_log (
    id         bigserial PRIMARY KEY,
    tenant_id  uuid NOT NULL REFERENCES tenants(id),
    user_id    uuid NOT NULL REFERENCES users(id),
    query      text NOT NULL,
    chunk_ids  uuid[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Session context helpers. current_setting(..., true) returns NULL when unset => policies deny.
CREATE FUNCTION app_tenant() RETURNS uuid LANGUAGE sql STABLE AS
    $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;
CREATE FUNCTION app_roles() RETURNS text[] LANGUAGE sql STABLE AS
    $$ SELECT coalesce(string_to_array(nullif(current_setting('app.roles', true), ''), ','), '{}') $$;

ALTER TABLE users     ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks    ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_users ON users
    USING (tenant_id = app_tenant());

-- Documents/chunks: read if same tenant AND at least one shared role.
-- Write only within your tenant, granting only roles you hold (no planting docs in HR's index).
CREATE POLICY tenant_role_docs ON documents
    USING (tenant_id = app_tenant() AND allowed_roles && app_roles())
    WITH CHECK (tenant_id = app_tenant() AND allowed_roles <@ app_roles());
CREATE POLICY tenant_role_chunks ON chunks
    USING (tenant_id = app_tenant() AND allowed_roles && app_roles())
    WITH CHECK (tenant_id = app_tenant() AND allowed_roles <@ app_roles());

CREATE POLICY tenant_audit ON audit_log
    USING (tenant_id = app_tenant())
    WITH CHECK (tenant_id = app_tenant());

-- Application role. Audit log is append-only: no UPDATE/DELETE granted.
CREATE ROLE app LOGIN PASSWORD 'app';  -- dev-only
GRANT SELECT ON tenants TO app;
GRANT SELECT ON users TO app;
GRANT SELECT, INSERT, DELETE ON documents, chunks TO app;
GRANT SELECT, INSERT ON audit_log TO app;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO app;
