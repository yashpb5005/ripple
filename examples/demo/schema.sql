-- The current schema. In real life this comes from your migrations or `pg_dump --schema-only`.
CREATE TABLE customers (
  id            bigserial PRIMARY KEY,
  name          text        NOT NULL,
  email         text        NOT NULL UNIQUE,
  customer_type text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE invoices (
  id          bigserial PRIMARY KEY,
  customer_id bigint        NOT NULL REFERENCES customers (id),
  amount      numeric(12,2) NOT NULL,
  status      text          NOT NULL DEFAULT 'open',
  created_at  timestamptz   NOT NULL DEFAULT now()
);
