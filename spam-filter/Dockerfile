FROM python:3.12-slim

WORKDIR /app

# wget is used in entrypoint.sh to probe the Ollama API
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# Persistent volume for the SQLite audit database
VOLUME ["/data"]

ENTRYPOINT ["./entrypoint.sh"]
