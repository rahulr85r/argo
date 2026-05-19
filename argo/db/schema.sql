-- Argo Phase 0 demo domain model.
-- Three users, four accounts (three individual + one joint), and the
-- transaction history that makes per-user output filtering meaningful.
-- Applied idempotently on app startup via argo.db.bootstrap.init_db().

CREATE TABLE IF NOT EXISTS users (
  id            text PRIMARY KEY,
  display_name  text NOT NULL,
  email         text NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS accounts (
  id            text PRIMARY KEY,
  display_name  text NOT NULL,
  account_type  text NOT NULL CHECK (account_type IN ('individual', 'joint')),
  last4         text NOT NULL,
  balance_cents bigint NOT NULL
);

CREATE TABLE IF NOT EXISTS account_owners (
  account_id  text NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  user_id     text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (account_id, user_id)
);

CREATE TABLE IF NOT EXISTS transactions (
  id                    text PRIMARY KEY,
  account_id            text NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  amount_cents          bigint NOT NULL,
  direction             text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
  counterparty_name     text,
  counterparty_user_id  text REFERENCES users(id),
  memo                  text,
  ts                    timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_account_owners_user ON account_owners(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_ts ON transactions(ts DESC);
