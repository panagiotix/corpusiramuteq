# syntax=docker/dockerfile:1
FROM python:3.11-slim

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

# Streamlit's default port.
EXPOSE 8501

# Lets `docker ps` / `docker inspect` report whether the app is actually up.
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
