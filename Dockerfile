# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Any UID can write here (used below), whether or not the container is
# run with --user. Needed because Streamlit writes a small internal
# file under $HOME/.streamlit at startup — without a writable HOME, an
# arbitrary non-root UID (e.g. from `docker run --user`) has no valid
# home directory, which crashes session startup with a
# "Permission denied: '/.streamlit'" error.
ENV HOME=/tmp

# System packages needed to build a couple of Python dependencies
# (trafilatura/lxml, matplotlib) on platforms without prebuilt wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY .streamlit/ .streamlit/

# Where "Recent extractions" (corpus, logs, stats for every past run) are
# stored. Mount this as a volume so it survives container restarts —
# see DOCKER.md. Works fine without a mount too; it just won't persist
# past `docker rm`.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# Streamlit's default port.
EXPOSE 8501

# Lets `docker ps` / `docker inspect` report whether the app is actually up.
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
