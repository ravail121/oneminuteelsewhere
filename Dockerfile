FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY config.yaml ./
COPY assets ./assets
RUN mkdir -p /app/data /app/output /app/secrets
ENTRYPOINT ["elsewhere"]
CMD ["doctor"]

