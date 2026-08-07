FROM ghcr.io/sanmibuh/python-trade-republic:latest

WORKDIR /app

RUN mkdir -p /app/data /app/output

COPY app /app/app

CMD ["python", "-m", "app.main"]
