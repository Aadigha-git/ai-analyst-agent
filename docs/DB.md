# Database Design

## Target database

The analyzed database is **external and user-owned**. Requirements: PostgreSQL, accessed through a read-only role. Schema is discovered at runtime via `introspect_schema`.

### Bring your own database

The bundled `docker compose up -d db` path is a **demo/seeded** instance for Quickstart and evaluation only.

For a real database:

1. Apply [`scripts/create_readonly_role.sql`](../scripts/create_readonly_role.sql) on **that** database (repeat for every new target — not a one-time setup).
2. Set `READONLY_DATABASE_URL` in `.env` to the read-only role’s connection string (see ADR-006). Keep `DATABASE_URL` for privileged admin/setup tasks if needed.
3. Author or extend [`config/glossary.yaml`](../config/glossary.yaml) with business terms for that schema (v2 ADR-007 / BR-11). The seeded glossary matches the sample retail DB only — BYO datasets should redefine `revenue`, `active_customer`, time-window defaults, etc.
4. Do **not** start the compose demo DB unless you want the sample retail seed.

The read-only role is the primary “never writes” control. Application-level SELECT validation is defense in depth; a write-capable role defeats the guarantee.

MCP clients pass `database_url` per `ask_data_question` call (overrides env for that request); still prefer a read-only URL.

## Sample / demo schema (retail/sales)

| Table | Key columns | Notes |
| --- | --- | --- |
| `customers` | `customer_id` (PK), `region`, `signup_date`, `segment` | Dimension for segmentation |
| `products` | `product_id` (PK), `category`, `unit_price` | Dimension |
| `orders` | `order_id` (PK), `customer_id` (FK), `order_date`, `channel` | One row per order; dates span ~2024-01 through 2025-06 in the seed |
| `order_items` | `order_item_id` (PK), `order_id` (FK), `product_id` (FK), `quantity`, `line_total` | Fact table; `revenue` glossary term = `SUM(line_total)` |

Seeded from `sample_db/seed.sql` (~200 orders / 80 customers / 40 products). Read-only role script: `scripts/create_readonly_role.sql`.

### Compose services

| Service | Role |
| --- | --- |
| `db` | Postgres 16 + seed + readonly role init |
| `app` | CLI image (`python -m cli`) |
| `mcp` | MCP stdio server image (`python -m mcp_server`) |
