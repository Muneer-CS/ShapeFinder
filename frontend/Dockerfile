FROM node:22-alpine AS build

ENV NPM_CONFIG_FUND=false \
    NPM_CONFIG_UPDATE_NOTIFIER=false

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

RUN VITE_API_BASE_URL= npm run build

FROM caddy:2.10-alpine

WORKDIR /app
COPY Caddyfile ./Caddyfile
COPY --from=build /app/dist ./dist

CMD ["caddy", "run", "--config", "/app/Caddyfile", "--adapter", "caddyfile"]
