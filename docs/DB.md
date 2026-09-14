# Database Design

## Target database

The analyzed database is **external and user-owned**. Requirements: PostgreSQL, accessed through a read-only role. Schema is discovered at runtime via `introspect_schema`.

### Bring your own database

The bundled `docker compose up -d db` path is a **demo/seeded** instance for Quickstart and evaluation only.

For a real database:

1. Apply [`scripts/create_readonly_role.sql`](../scripts/create_readonly_role.sql) on **that** database (repeat for every new target — not a one-time setup).
2. Set `READONLY_DATABASE_URL` in `.env` to the read-only role’s connection string (see ADR-006). Keep `DATABASE_URL` for privileged admin/setup tasks if needed.
3. Do **not** start the compose demo DB unless you want the sample retail seed.

The read-only role is the primary “never writes” control. Application-level SELECT validation is defense in depth; a write-capable role defeats the guarantee.

## Sample / demo schema (retail/sales)

| Table | Key columns | Notes |
| --- | --- | --- |
| `customers` | `customer_id` (PK), `region`, `signup_date`, `segment` | Dimension for segmentation |
| `products` | `product_id` (PK), `category`, `unit_price` | Dimension |
| `orders` | `order_id` (PK), `customer_id` (FK), `order_date`, `channel` | One row per order |
| `order_items` | `order_item_id` (PK), `order_id` (FK), `product_id` (FK), `quantity`, `line_total` | Fact table |

Seeded from `sample_db/seed.sql` (~200 orders). Read-only role script: `scripts/create_readonly_role.sql`.
