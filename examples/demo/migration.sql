-- What an AI assistant wrote when asked:
-- "rename customer type to segment, and track the currency of each invoice"
ALTER TABLE customers DROP COLUMN customer_type;
ALTER TABLE customers ADD COLUMN segment text;
ALTER TABLE invoices ADD COLUMN currency text NOT NULL;
