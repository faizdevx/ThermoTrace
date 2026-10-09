# Production frontend: build with Vite, serve with nginx, proxy /api to the backend.
FROM node:22-alpine AS build
WORKDIR /app
COPY industrial-gis/frontend/package.json industrial-gis/frontend/package-lock.json ./
RUN npm ci
COPY industrial-gis/frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
