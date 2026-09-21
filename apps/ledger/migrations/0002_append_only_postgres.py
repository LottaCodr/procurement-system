"""Postgres-level append-only enforcement. Dev/test on SQLite cannot carry these
statements, so this migration is a no-op there and hard enforcement in production.

The ORM refusal in `LedgerEventQuerySet` is the belt; table grants and triggers are
the braces. A DBA with the application role can still reach the database with
`psql`, so the guarantee has to exist below the ORM.
"""
from django.db import migrations, connection


FORWARD = """
CREATE OR REPLACE FUNCTION ledger_event_refuse_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger_event is append-only: % is forbidden (corrections are new events)', TG_OP
    USING ERRCODE = '42001';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_event_no_update
    BEFORE UPDATE ON ledger_event
    FOR EACH ROW EXECUTE FUNCTION ledger_event_refuse_mutation();

CREATE TRIGGER ledger_event_no_delete
    BEFORE DELETE ON ledger_event
    FOR EACH ROW EXECUTE FUNCTION ledger_event_refuse_mutation();

-- The application role gets no mutation rights at all.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'taraba_app') THEN
        EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON ledger_event FROM taraba_app';
        EXECUTE 'GRANT INSERT, SELECT ON ledger_event TO taraba_app';
    END IF;
END$$;

-- Sequence guard: seq must be strictly increasing with no gaps usable for
-- "rewrite the tail". Enforced in the trigger above; asserted by verify_chain.
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]
    operations = [
        migrations.RunSQL(sql=migrations.RunSQL.noop if connection.vendor != "postgresql" else FORWARD, reverse_sql="""
            DROP TRIGGER IF EXISTS ledger_event_no_update ON ledger_event;
            DROP TRIGGER IF EXISTS ledger_event_no_delete ON ledger_event;
            DROP FUNCTION IF EXISTS ledger_event_refuse_mutation();
        """),
    ]

