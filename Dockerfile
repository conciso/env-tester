FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    docker.io \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/env-tester

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY env_tester.py .
COPY presets.yml .

ENTRYPOINT ["python3", "/opt/env-tester/env_tester.py"]
