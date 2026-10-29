-- The same goal, done safely: add first, copy the data, keep the old column
-- until every app has moved over, and give the new required column a default.
ALTER TABLE customers ADD COLUMN segment text;
UPDATE customers SET segment = customer_type WHERE segment IS NULL;
ALTER TABLE invoices ADD COLUMN currency text NOT NULL DEFAULT 'INR';
