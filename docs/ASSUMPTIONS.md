# Assumptions and design decisions

These are the choices made where the brief left room for interpretation.

## General

- **No authentication or authorization.** The brief doesn't ask for it. In a real system, every endpoint would need it, especially the account list.
- **One currency, two decimal places.** Amounts and balances are stored as `NUMERIC(18,2)` and returned as strings (`"150.00"`), so clients never lose precision to floating-point numbers.
- **The server generates IDs and timestamps.** IDs are UUIDv4. `created_at` is set by the server in UTC when the row is inserted.
- **Strict input.** A request body field the endpoint doesn't accept is rejected with `400` rather than ignored. This also covers `balance` when creating an account, which the brief says the client can't set. Unknown query parameters are ignored.
- **One error format** everywhere, including unknown routes and server errors: `{"error": {"code", "message", "details"}}`.

## Accounts

- `name` is required and trimmed, 1–255 characters, and **doesn't have to be unique**: two people can share a name.
- Accounts can't be renamed or deleted. Neither is in the brief, and deleting an account would orphan its ledger history.
- An account ID in the URL can be written in any standard UUID form (uppercase, with or without hyphens). An ID that isn't a UUID returns `400 INVALID_ACCOUNT_ID`. A valid UUID that matches no account returns `404 ACCOUNT_NOT_FOUND`.

## Amounts

- An amount must be greater than 0 and have at most 2 decimal places. That allows `0.01`, but not `0.001`.
- It can be a JSON number or a plain decimal string (`100`, `100.50`). Forms that Python would parse but nobody writes money in (`1e2`, `+5`, `" 7 "`, `1_000`, non-ASCII digits) are rejected.
- **A single amount can be at most 9,999,999,999.99.** The balance column holds up to 9,999,999,999,999,999.99. A credit that would go past that is rejected with `422 BALANCE_LIMIT_EXCEEDED` rather than overflowing.

## Transactions

- `type` must be exactly `credit` or `debit`, in lowercase.
- `description` is optional (empty if left out), trimmed, and up to 255 characters.
- **An insufficient balance returns `422`** with the message "Insufficient funds." The `details` include the balance, the requested amount and the shortfall. I chose `422` because the request is well-formed but breaks a business rule.

## `reference_id` and duplicates

- The client generates `reference_id` (for example from its own order or invoice number) and reuses it when retrying. It's required, trimmed, 1–100 characters, and **case-sensitive**, so `REF-1` and `ref-1` are different.
- It must be unique **per account**, as the brief requires. The same `reference_id` can be used on different accounts.
- **A duplicate returns `409`, with the original transaction in `details`,** and changes nothing. This holds even if the repeated request has a different amount or type: the reference identifies the transaction, and silently accepting a changed retry would hide a client bug.
- **The duplicate check runs before the balance check.** A retried debit that already succeeded must say "duplicate", not "insufficient funds".
- **Rejected requests don't use up their `reference_id`.** Validation errors, `404` and `422` store nothing, so the client can retry with the same reference once the problem is fixed.

## History and pagination

- Results are sorted newest first by `created_at`. When two transactions share a timestamp, `id` decides the order, so a record never appears on two pages.
- `page` defaults to 1, and `limit` defaults to 10 with a maximum of 100. Invalid values return `400` instead of being quietly corrected.
- A page past the end returns `200` with an empty `data` list, along with the real totals.

## Transfers (bonus)

- A transfer is stored as one `Transfer` record plus **two ordinary transactions**: a debit on the source and a credit on the destination. Each account's history therefore shows every movement of money, and `transfer_id` links the two legs.
- `reference_id` is unique per **source** account for transfers. Because both legs carry it, it also has to be unused on both accounts; otherwise the request gets `409 DUPLICATE_TRANSACTION`.
- Transferring to the same account is a validation error (`400`). If an account doesn't exist, the `404` names the field (`from_account_id` or `to_account_id`). If both are missing, the source is reported.
- If no `description` is given, the legs read "Transfer to {name}" and "Transfer from {name}".

## Data integrity

- **Atomicity:** each write (a transaction and its balance update, or a transfer's record, two legs and two balance updates) runs in a single database transaction.
- **Concurrency:** writes lock the affected account rows (`SELECT … FOR UPDATE`) under PostgreSQL's default READ COMMITTED isolation. Transfers lock both rows in sorted ID order, which rules out deadlocks between opposite transfers. The tests prove this with real parallel transactions.
- **Constraints:** these are defined in the database, so they hold even if application code is bypassed:
  - balance ≥ 0
  - amount > 0
  - a valid type
  - non-blank references
  - unique `(account, reference_id)` and `(source account, reference_id)`
  - a transfer's two accounts must differ
  - at most one debit leg and one credit leg per transfer
- **Immutability:** the API has no update or delete endpoints for transactions or transfers, and PostgreSQL triggers reject `UPDATE` and `DELETE` on those tables. To correct a mistake, you post a new, opposite transaction. `TRUNCATE` isn't blocked, so `seed --reset` and test cleanup still work.

## Beyond the brief

- **`GET /accounts/{account_id}`** returns the full account. The brief's balance endpoint returns only `account_id` and `balance`, which leaves no way to read an account's `name` or `created_at` after creating it. A client needs those to confirm whose account an ID belongs to, for example before sending a transfer to it.
- **`GET /accounts`** returns a paginated list of accounts. Without it, an account's ID appears only in the create response, so a client that didn't store it, or anyone exploring the API, can't find the account again. A real deployment would limit this list to administrators, because it shows every account.
- **`python manage.py seed`** loads demo accounts with fixed IDs. Running it again is safe, and `--reset` restores a clean state.
- **Other HTTP errors:**
  - `405` for unsupported methods, with an `Allow` header
  - `406` when the client won't accept JSON
  - `413` for request bodies over 2.5 MB
  - `415` for bodies that aren't JSON
  - a generic `500` that never leaks internal details
