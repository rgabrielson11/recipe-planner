FROM python:3.12-slim

WORKDIR /app

# OCI standard image labels
LABEL org.opencontainers.image.title="Recipe Planner" \
      org.opencontainers.image.description="Self-hosted weekly dinner meal planner with Mealie integration" \
      org.opencontainers.image.url="http://localhost:8111" \
      org.opencontainers.image.icon="mdi-silverware-fork-knife"

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Data directory for SQLite db + YAML configs
RUN mkdir -p /app/data

EXPOSE 8111

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8111", "--reload"]
