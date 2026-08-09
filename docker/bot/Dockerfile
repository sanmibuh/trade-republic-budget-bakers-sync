ARG BASE_TAG=latest
FROM ghcr.io/sanmibuh/python-trade-republic:${BASE_TAG}

WORKDIR /app

# Install Docker CLI so the bot can run `docker exec` on sibling containers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY app /app/app

CMD ["python", "-m", "app", "bot"]
