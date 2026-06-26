# Caddy image that serves the built SPA and proxies /api. TLS is handled at
# Cloudflare's edge via the tunnel, so plain upstream Caddy is enough (no DNS
# plugin needed). Stage 1 builds the Vite bundle; stage 2 bakes it into /srv.
FROM node:20-slim AS build
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /fe/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
