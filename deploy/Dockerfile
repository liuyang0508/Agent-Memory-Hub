FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY agent_brain/ ./agent_brain/
COPY agent_runtime_kit/ ./agent_runtime_kit/
COPY web/ ./web/

RUN pip install --no-cache-dir -e ".[all]" 2>/dev/null || pip install --no-cache-dir -e .

RUN useradd -m -s /bin/bash memhub && \
    mkdir -p /data/brain && chown -R memhub:memhub /data

USER memhub

ENV BRAIN_DIR=/data/brain
ENV MEMORY_HUB_NO_MODEL=0

VOLUME ["/data/brain"]

EXPOSE 8742

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "from agent_brain.memory.store.items_store import ItemsStore; ItemsStore('/data/brain/items')" || exit 1

CMD ["python", "-m", "agent_brain.interfaces.cli", "serve", "--host", "0.0.0.0", "--port", "8742"]
