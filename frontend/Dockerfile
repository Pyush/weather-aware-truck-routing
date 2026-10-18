# Frontend image.
#
# Builds the Vite bundle, then serves it from nginx with the SPA fallback and
# an /api proxy. nginx is not just "the static server": it is the piece that
# makes the streaming endpoint work, because it has to forward the response
# body as it arrives rather than buffering the whole thing.

FROM node:22-slim AS builder

WORKDIR /build

# Copied before the source so the install layer survives code-only changes.
COPY package.json package-lock.json ./
RUN npm ci

COPY . ./
RUN npm run build


FROM nginx:1.27-alpine AS runtime

# Replace the stock config entirely: the default one has no SPA fallback and no
# API proxy, both of which the app needs.
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1