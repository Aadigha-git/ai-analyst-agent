# Database Design

## Target database

The analyzed database is external and user-owned. Requirements: PostgreSQL, accessed through a read-only role. Schema is discovered at runtime via `introspect_schema`.

## Sample / demo schema (retail/sales)

| Table | Key columns | Notes |
| --- | --- | --- |
| `customers` | `customer_id` (PK), `region`, `signup_date`, `segment` | Dimension for segmentation |
| `products` | `product_id` (PK), `category`, `unit_price` | Dimension |
| `orders` | `order_id` (PK), `customer_id` (FK), `order_date`, `channel` | One row per order |
| `order_items` | `order_item_id` (PK), `order_id` (FK), `product_id` (FK), `quantity`, `line_total` | Fact table |

Seeded from `sample_db/seed.sql` (~200 orders). Read-only role script: `scripts/create_readonly_role.sql`.
