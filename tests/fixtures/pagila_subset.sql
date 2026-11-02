-- Trimmed, simplified subset of the Pagila sample database (table/column names only;
-- no data, no original comments or full DDL carried over).
--
-- Source: https://github.com/devrimgunduz/pagila
-- License: PostgreSQL License (https://opensource.org/license/postgresql/) — a permissive,
-- MIT-style license. See CONTRIBUTING.md "Test data provenance" for details.
--
-- Used only by tests/test_dependencies.py, which needs real views, trigger functions, and
-- foreign keys to exercise Ripple's "what blocks this drop" detection — the custom schema
-- in examples/demo intentionally has none of these, so it can't be used for this.

CREATE TABLE customer (
  customer_id serial PRIMARY KEY,
  first_name  text        NOT NULL,
  last_name   text        NOT NULL,
  email       text        NOT NULL UNIQUE,
  last_update timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE payment (
  payment_id   serial PRIMARY KEY,
  customer_id  integer       NOT NULL REFERENCES customer (customer_id),
  amount       numeric(10,2) NOT NULL,
  payment_date timestamptz   NOT NULL DEFAULT now()
);

-- Depends on customer.email: dropping that column should be blocked, and Ripple should
-- be able to name this view as the reason.
CREATE VIEW customer_list AS
  SELECT customer_id, first_name || ' ' || last_name AS name, email FROM customer;

-- References customer.last_update in its body. Postgres does NOT dependency-track columns
-- referenced inside a plpgsql function body, so dropping last_update won't be blocked here —
-- this is exactly the gap the function-body scan is meant to catch as a best-effort warning.
CREATE FUNCTION last_updated() RETURNS trigger AS $$
BEGIN
  NEW.last_update = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER customer_last_update
  BEFORE UPDATE ON customer
  FOR EACH ROW EXECUTE FUNCTION last_updated();
