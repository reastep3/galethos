FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY galethos ./galethos
COPY app.py ./
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

EXPOSE 8080

# Single worker so scan state/results live in one process; threads for I/O.
# Long timeout: a scan takes ~60s but runs in its own thread, requests stay fast.
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--workers", "1", "--threads", "8", \
     "--timeout", "120", "app:app"]
