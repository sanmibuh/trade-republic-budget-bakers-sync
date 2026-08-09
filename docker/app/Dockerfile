ARG BASE_TAG=v4.0.0
FROM ghcr.io/sanmibuh/python-trade-republic:${BASE_TAG}

WORKDIR /app

RUN mkdir -p /app/data /app/output \
    && apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

COPY app /app/app
COPY docker/app/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD []
