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

# Bake version string into image
COPY VERSION /app/VERSION

# Bake a copy of the YAML defaults into a non-mounted location so the
# entrypoint can always reach them regardless of host volume mounts.
# /app/app/data is the live data dir (mounted from host in production);
# /app/defaults is never mounted and always reflects the current image.
RUN mkdir -p /app/defaults
COPY app/data/ /app/defaults/

# Data directory for SQLite db + live YAML configs (mounted from host)
RUN mkdir -p /app/data /app/app/data

# Entrypoint syncs code-owned YAMLs on every start, then launches uvicorn
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8111

ENTRYPOINT ["/app/entrypoint.sh"]
