# Production frontend: build with Vite, serve with nginx, proxy /api to the backend.
FROM node:22-alpine AS build
WORKDIR /app
COPY industrial-gis/frontend/package.json industrial-gis/frontend/package-lock.json ./
RUN npm ci
COPY industrial-gis/frontend/ ./
# optional own tile server for production traffic (the OSM volunteer servers are for light use)
ARG VITE_TILE_URL=
ENV VITE_TILE_URL=$VITE_TILE_URL
# self-hosted vector basemap (docker-compose.offline.yml): the app reads this PMTiles file instead of any public tile server
ARG VITE_PMTILES_URL=
ENV VITE_PMTILES_URL=$VITE_PMTILES_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
