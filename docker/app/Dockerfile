FROM ghcr.io/sanmibuh/python-trade-republic:v1.0.0

WORKDIR /app

RUN mkdir -p /app/data /app/output

COPY app /app/app

CMD ["python", "-m", "app.main"]
