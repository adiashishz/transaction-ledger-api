# Transaction Ledger API

A REST API for a simple transaction ledger. You can create accounts, post credits and debits, and read balances and paginated transaction history. You can also transfer money between accounts. Built with Django 5.2, Django REST Framework and PostgreSQL.

## Requirements

- Python 3.12 or newer
- PostgreSQL 14 or newer, **or** Docker with Compose v2 (the `docker compose` command) to run PostgreSQL in a container

## Run it locally

Run these in order from a terminal.

**1. Get the code:**

```bash
git clone https://github.com/adiashishz/transaction-ledger-api.git
cd transaction-ledger-api
```

**2. Create your `.env` file:**

```bash
cp .env.example .env               # Windows: copy .env.example .env
```

The defaults work as they are with either option in step 3. If your PostgreSQL runs on another host or port, or you want different credentials, edit the `DB_*` values in `.env` **before** step 3.

**3. Start PostgreSQL.** Pick one option:

- **A. With Docker** (no PostgreSQL install needed):

  ```bash
  docker compose up -d --wait
  ```

  This starts PostgreSQL 17 using the database name, user and password from `.env`, and waits until it's ready. It's reachable from this machine only.
  - **Already running PostgreSQL locally on port 5432?** Set `DB_PORT=5433` in `.env` first.
  - The user, password and database are created only on the very first start. If you change them in `.env` later, run `docker compose down -v` and start again.
  - `docker compose down` stops it, and keeps the data for next time.
  - `docker compose down -v` stops it and deletes the data.

- **B. With your own PostgreSQL:** create the user and database once:

  ```bash
  sudo -u postgres psql -c "CREATE ROLE ledger WITH LOGIN PASSWORD 'ledger' CREATEDB;"
  sudo -u postgres psql -c "CREATE DATABASE ledger OWNER ledger;"
  ```

  On macOS or Windows, run the two SQL statements in `psql` as your Postgres superuser instead. `CREATEDB` lets the test runner create its own test database. If you changed `DB_NAME`, `DB_USER` or `DB_PASSWORD` in `.env`, use the same values here.

**4. Create a virtual environment and install the dependencies:**

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**5. Create the tables:**

```bash
python manage.py migrate
```

**6. Load the demo data:**

```bash
python manage.py seed
```

**7. Start the server:**

```bash
python manage.py runserver         # port 8000 taken? python manage.py runserver 8001
```

**8. Open <http://localhost:8000/api/docs>.** This Swagger page lists every endpoint, and "Try it out" lets you call each one from the browser. The demo accounts are offered as example IDs.

## Demo data

`python manage.py seed` creates four accounts with fixed IDs. Running it again is safe. `python manage.py seed --reset` deletes all ledger data and seeds again from scratch.

| Account | ID | Balance | Use it to try |
|---|---|---|---|
| Aarav Sharma | `00000000-0000-4000-8000-000000000001` | 100.00 | The brief's example: +100 +50 −30 −20 |
| Priya Patel | `00000000-0000-4000-8000-000000000002` | 4495.00 | Pagination: 31 transactions (4 pages), including a transfer out |
| Rohan Mehta | `00000000-0000-4000-8000-000000000003` | 0.00 | Rejected debits: the account is empty |
| Ananya Iyer | `00000000-0000-4000-8000-000000000004` | 950.00 | A transfer received from Priya |

## Try it from the command line

```bash
# Balance
curl localhost:8000/api/accounts/00000000-0000-4000-8000-000000000001/balance

# Credit 25.00
curl -X POST localhost:8000/api/accounts/00000000-0000-4000-8000-000000000001/transactions \
  -H 'Content-Type: application/json' \
  -d '{"type": "credit", "amount": "25.00", "reference_id": "INV-1001"}'

# Send the same request again: 409, and the balance doesn't change
curl -X POST localhost:8000/api/accounts/00000000-0000-4000-8000-000000000001/transactions \
  -H 'Content-Type: application/json' \
  -d '{"type": "credit", "amount": "25.00", "reference_id": "INV-1001"}'

# Debit an empty account: 422 Insufficient funds
curl -X POST localhost:8000/api/accounts/00000000-0000-4000-8000-000000000003/transactions \
  -H 'Content-Type: application/json' \
  -d '{"type": "debit", "amount": "10.00", "reference_id": "PAY-1"}'

# History: credits only, page 1, 5 per page
curl "localhost:8000/api/accounts/00000000-0000-4000-8000-000000000002/transactions?type=credit&page=1&limit=5"

# Transfer 30.00 from Priya to Rohan
curl -X POST localhost:8000/api/transfers \
  -H 'Content-Type: application/json' \
  -d '{"from_account_id": "00000000-0000-4000-8000-000000000002", "to_account_id": "00000000-0000-4000-8000-000000000003", "amount": "30.00", "reference_id": "TRF-1"}'
```

## Run the tests

```bash
python manage.py test
```

There are 162 tests. They include concurrency tests that run real parallel database transactions.

## How the ledger stays consistent

- **Atomic writes:** a transaction row and its balance update are saved in one database transaction. A transfer saves its record, both legs and both balance updates the same way. Nothing is ever half-applied.
- **Row locks:** every write locks the account row first (`SELECT … FOR UPDATE`), so two debits at the same moment can't both pass the balance check. Transfers lock both accounts in a fixed order, so two transfers in opposite directions can't deadlock.
- **Safe retries:** `reference_id` is unique per account, enforced by a database constraint. If a client sends the same request twice, the second one gets `409` with the original transaction, and nothing is posted twice.
- **Database constraints as a backstop:** balance ≥ 0, amount > 0, a valid transaction type and unique references are all enforced by `CHECK` and `UNIQUE` constraints, even if the application code has a bug.
- **Immutable history:** the API has no way to edit or delete transactions or transfers. Database triggers also reject `UPDATE` and `DELETE` on those tables.

## Project layout

```
config/                     Django settings and root URLs
apps/ledger/
  models.py                 Account, Transaction, Transfer and their constraints
  serializers.py            Request validation and response shapes
  services.py               Business rules: locking, atomic writes, duplicates, balance checks
  views.py, urls.py         HTTP layer
  exceptions.py             Error types and the shared error format
  pagination.py             page/limit pagination
  migrations/               Tables, constraints and immutability triggers
  management/commands/      The seed command
  tests/                    Test suite
docs/                       API reference and assumptions
docker-compose.yml          Optional PostgreSQL container
```

## Troubleshooting

- **Port 8000 is busy:** run `python manage.py runserver 8001` and use port 8001 in the URLs above.
- **`docker compose up` fails with "address already in use" or "port is already allocated":** something else, usually a local PostgreSQL, is already using port 5432. Set `DB_PORT=5433` (or any free port) in `.env` and run the command again. Docker and Django both read that value.
- **`migrate` fails with "password authentication failed" or 'role "ledger" does not exist':**
  - *With Docker (option A):* either `.env` changed after the database was first created (run `docker compose down -v`, then `docker compose up -d --wait`), or a local PostgreSQL is answering on the same port instead of the container. This can happen on macOS even when Docker started without errors; fix it with a different `DB_PORT`.
  - *With your own PostgreSQL (option B), or "Ident authentication failed":* your PostgreSQL only allows OS-user logins over TCP. Change `ident` or `peer` to `scram-sha-256` on the `host` lines of `pg_hba.conf`, then restart PostgreSQL.

## Documentation

- [docs/API.md](docs/API.md): every endpoint, with request and response examples and error codes
- [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md): the assumptions and design decisions behind this implementation
- Swagger UI at `/api/docs`, and the OpenAPI schema at `/api/schema`
