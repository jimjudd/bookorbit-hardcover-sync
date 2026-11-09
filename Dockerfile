FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
COPY main.py ./
# Run one sync on demand: docker exec bookorbit-hardcover-sync sync-now
RUN printf '#!/bin/sh\ncd /app && exec /app/.venv/bin/python main.py --once\n' > /usr/local/bin/sync-now && chmod +x /usr/local/bin/sync-now
CMD ["uv", "run", "--no-dev", "python", "main.py"]
