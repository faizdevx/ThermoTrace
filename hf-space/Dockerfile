# ThermoTrace as one container for a Hugging Face Docker Space (free CPU tier, no persistent disk).
# PostGIS, the API, the FIRMS thermal worker and nginx run side by side; start.sh loads the demo bundle on every start.
# Build context (the Space repository): this Dockerfile, README.md and the demo bundle files at the top level
# (*.csv.gz and thermal_baseline.parquet from deploy/demo_bundle.py make), so they can be uploaded through the web page.
FROM node:22-bookworm-slim AS web
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
ARG REPO=https://github.com/faizdevx/ThermoTrace
ARG REF=deployment
RUN git clone --depth 1 -b "$REF" "$REPO" /src
WORKDIR /src/industrial-gis/frontend
RUN npm ci && npm run build

FROM debian:trixie-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      postgresql postgresql-17-postgis-3 nginx python3 python3-venv ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=web /src /src
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /src/industrial-data/requirements.txt -r /src/industrial-gis/backend/requirements.txt
COPY --from=web /src/industrial-gis/frontend/dist /srv/www
COPY *.csv.gz /bundle/
COPY thermal_baseline.parquet /bundle/baseline/
# Spaces run the container as uid 1000
RUN useradd -m -u 1000 user && mkdir -p /data/thermal/live /data/thermal/imagery /data/thermal/facilities /data/baseline \
    && chown -R user /data /src /srv/www
USER user
ENV PATH=/opt/venv/bin:/usr/lib/postgresql/17/bin:$PATH
EXPOSE 7860
CMD ["bash", "/src/hf-space/start.sh"]
