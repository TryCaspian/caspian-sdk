# Caspian gateway (self-host). Build context is ./server.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Bind to all interfaces inside the container; override the rest via env.
ENV COMM_HOST=0.0.0.0 \
    COMM_PORT=8000

EXPOSE 8000
CMD ["comm-gateway"]
