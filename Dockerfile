FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Jakarta

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates ffmpeg libmagic1 tzdata postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# CLI docker statis untuk tool `sandbox`; daemon tetap di host (socket di-mount terpisah).
ARG DOCKER_CLI_VERSION=27.5.1
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl --proto '=https' --proto-redir '=https' -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && chmod +x /usr/local/bin/docker \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# uv memasang dependency dari uv.lock (versi + hash terkunci), bukan resolusi pip bebas.
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-build --no-install-project --extra whatsapp --extra vibe --extra memory

COPY sanka ./sanka
RUN uv sync --locked --no-build --extra whatsapp --extra vibe --extra memory

ENV PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/app/.sanka/hf

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin sanka \
    && mkdir -p /app/.sanka \
    && chown -R sanka:sanka /app

# Harus SETELAH useradd: grup docker mengikuti gid socket di host.
ARG DOCKER_GID=994
RUN groupadd -g "${DOCKER_GID}" docker || true \
    && usermod -aG docker sanka

USER sanka
# Model embedder Mem0 diunduh sekali saat build ke cache milik user sanka.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

STOPSIGNAL SIGINT
# `sanka run` membuat schema, men-seed dokumen, lalu acquire lease primary.
CMD ["python", "-m", "sanka", "run"]
