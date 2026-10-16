# FastAPI backend. Build context: repository root.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
COPY industrial-gis/backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY industrial-gis/backend/ /srv/backend/
# imagery core is shared with the worker (one implementation of the scene / index logic)
COPY industrial-data/src/imagery/ /srv/src/imagery/
COPY industrial-data/src/alerts/ /srv/src/alerts/
COPY industrial-data/src/netguard.py /srv/src/netguard.py
RUN touch /srv/src/__init__.py
RUN useradd --create-home --uid 10001 thermo
USER thermo
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
