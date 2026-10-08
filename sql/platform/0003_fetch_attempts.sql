-- O detail pedido por uma revisão tem que ser pelo menos tão novo quanto ela (o detail pode
-- vir de cache). Abaixo disso, o pedido fica e conta tentativa.
ALTER TABLE control.fetch_request ADD COLUMN min_updated timestamptz;

-- Tentativas de grid com falha transitória: limite e fila justa entre os produtos.
CREATE TABLE control.grid_attempt (
    stream_scope control.stream_scope NOT NULL,
    product_revision_id text NOT NULL,
    attempts int NOT NULL,
    last_error text,
    last_attempt_at timestamptz NOT NULL,
    PRIMARY KEY (stream_scope, product_revision_id)
);
