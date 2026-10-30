# PostgreSQL 17 + pgBackRest for production: continuous WAL archiving and backups to a local and an
# off-site repository (ADR 013). Development and CI keep using postgres:17-alpine from docker-compose.yml.
FROM postgres:17-bookworm

# pgBackRest comes from the PGDG repository already configured in the official image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends pgbackrest \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o postgres -g postgres -m 750 /var/lib/pgbackrest /var/spool/pgbackrest /var/log/pgbackrest \
    && install -d -o postgres -g postgres -m 750 /var/lib/districom-dumps

COPY pgbackrest.conf /etc/pgbackrest/pgbackrest.conf

# Refuses to start PostgreSQL without the prepared backup disk, on every start (districom-entrypoint.sh).
COPY districom-entrypoint.sh /usr/local/bin/districom-entrypoint.sh
ENTRYPOINT ["districom-entrypoint.sh"]
CMD ["postgres"]
