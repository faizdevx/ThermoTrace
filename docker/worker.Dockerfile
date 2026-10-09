# Sync worker: osm2pgsql (apt, Debian 13) + the industrial-data Python package.
FROM debian:trixie-slim
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      osm2pgsql python3 python3-venv python3-pip ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/venv
COPY industrial-data/requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /app
COPY industrial-data/ /app/
COPY docker/osm/thermotrace.lua /opt/thermotrace/thermotrace.lua
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/app
RUN useradd --create-home --uid 10001 thermo && mkdir -p /data/osm && chown -R thermo /data /app
USER thermo
ENTRYPOINT ["python", "-m", "src.sync.cli"]
CMD ["run"]
