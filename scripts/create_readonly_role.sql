-- Create a SELECT-only role for the agent (defense-in-depth with SQL guardrails).
-- Run as a superuser / schema owner against the target database.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'analyst_readonly') THEN
        CREATE ROLE analyst_readonly LOGIN PASSWORD 'change_me';
    END IF;
END
$$;

-- Strip any privileges that may already exist, then grant SELECT only.
REVOKE ALL ON SCHEMA public FROM analyst_readonly;
GRANT USAGE ON SCHEMA public TO analyst_readonly;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM analyst_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst_readonly;

REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM analyst_readonly;
-- No INSERT/UPDATE/DELETE/DDL: do not grant write or ownership privileges.

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO analyst_readonly;

-- Explicitly ensure no write defaults
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM analyst_readonly;
