# Caddy with the Cloudflare DNS module: certificates by DNS-01, without opening any port to the Internet (ADR 013).
FROM caddy:2.10-builder AS build
ARG CADDY_DNS_CLOUDFLARE_VERSION=v0.2.1
RUN xcaddy build --with github.com/caddy-dns/cloudflare@${CADDY_DNS_CLOUDFLARE_VERSION}

FROM caddy:2.10
COPY --from=build /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
