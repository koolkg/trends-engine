# Stage 1 (build): node + node_modules inside the image, never on the host.
# The unit tests and type-check run in this stage's image (compose service te-ui-test).
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY babel.config.js tsconfig.json webpack.config.js ./
COPY public/ public/
COPY src/ src/
COPY tests/ tests/
RUN npm run build

# Stage 2: nginx serves the static bundle and proxies /v1 (nginx.conf).
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
