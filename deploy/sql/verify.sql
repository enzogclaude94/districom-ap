-- Fingerprint of a DistriCom database, compared between production and a restored copy (deploy/bin/restore-drill)
-- and before/after a rollback (deploy/bin/rollback). Same output on both sides = same schema version, same
-- structure and, table by table, the same rows with the same values: every base table of every application
-- schema (platform, Comercial, Rendiciones and any future module), not a sample.
-- Deterministic: rows are hashed one by one and the hashes sorted, so physical order (VACUUM, a restore) does not
-- matter, and the text form of dates, times, numbers and binary values is fixed below. Sequences are left out on
-- purpose: PostgreSQL logs them ahead, so a restored sequence may legitimately be a few values further on.
-- Read-only: it creates nothing in the database.
\set ON_ERROR_STOP on
\set QUIET on
\pset tuples_only on
\pset format unaligned
\pset fieldsep ' '
SET TimeZone = 'UTC';
SET DateStyle = 'ISO, YMD';
SET IntervalStyle = 'postgres';
SET extra_float_digits = 3;
SET bytea_output = 'hex';

SELECT 'alembic_version', version_num FROM public.alembic_version;

-- Structure: columns, constraints, indexes, triggers, views and functions of the application schemas.
SELECT 'structure_md5', md5(string_agg(item, E'\n' ORDER BY item))
FROM (
    SELECT format('column %s.%s.%s %s %s %s', table_schema, table_name, column_name, data_type,
                  is_nullable, coalesce(column_default, '')) AS item
    FROM information_schema.columns
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema') AND table_schema NOT LIKE 'pg\_%'
    UNION ALL
    SELECT format('constraint %s.%s %s', n.nspname, c.conname, pg_get_constraintdef(c.oid))
    FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\_%'
    UNION ALL
    SELECT format('index %s', pg_get_indexdef(i.indexrelid))
    FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\_%'
    UNION ALL
    SELECT format('trigger %s', pg_get_triggerdef(t.oid))
    FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE NOT t.tgisinternal AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\_%'
    UNION ALL
    SELECT format('function %s.%s %s', n.nspname, p.proname, md5(pg_get_functiondef(p.oid)))
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prokind IN ('f', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\_%'
    UNION ALL
    SELECT format('view %s.%s %s', schemaname, viewname, md5(definition))
    FROM pg_views
    WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
) structure;

-- Data: for every base table, its row count and a hash of all its rows and columns.
SELECT format(
    'SELECT %L, count(*), coalesce(md5(string_agg(h, '''' ORDER BY h)), ''empty'') FROM (SELECT md5(t::text) AS h FROM %I.%I t) rows',
    'table ' || n.nspname || '.' || c.relname, n.nspname, c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\_%'
ORDER BY n.nspname, c.relname
\gexec

-- Readable business check on top of the hashes: balance of every ledger account.
SELECT 'balance', account_id, sum(amount)
FROM settlements.ledger_entries
GROUP BY account_id
ORDER BY account_id;
