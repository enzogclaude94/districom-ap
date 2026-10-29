-- Fingerprint of a DistriCom database, compared between production and a restored copy (deploy/bin/restore-drill).
-- Same output on both sides = same schema version, same rows per table and the same ledger.
\pset tuples_only on
\pset format unaligned
\pset fieldsep ' '

SELECT 'alembic_version', version_num FROM public.alembic_version;

SELECT 'rows', table_schema || '.' || table_name,
       (xpath('/row/c/text()',
              query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text
FROM information_schema.tables
WHERE table_type = 'BASE TABLE' AND table_schema IN ('public', 'settlements', 'commercial')
ORDER BY table_schema, table_name;

-- The ledger, entry by entry (immutable and append-only, so any loss or change shows up here).
SELECT 'ledger_entries_md5', coalesce(md5(string_agg(id || ':' || operation_id || ':' || account_id || ':' || amount, ',' ORDER BY id)), 'empty')
FROM settlements.ledger_entries;

SELECT 'balance', account_id, sum(amount)
FROM settlements.ledger_entries
GROUP BY account_id
ORDER BY account_id;
