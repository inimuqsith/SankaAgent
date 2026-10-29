FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Jakarta

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates ffmpeg libmagic1 tzdata postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# CLI docker statis untuk tool `sandbox`; daemon tetap di host (socket di-mount terpisah).
ARG DOCKER_CLI_VERSION=27.5.1
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
       | tar -xz --strip-components=1 -C /usr/local/bin docker/docker \
    && chmod +x /usr/local/bin/docker \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY sanka ./sanka
# torch CPU dulu: wheel default membawa CUDA dan jauh lebih besar.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch
RUN pip install --no-cache-dir ".[whatsapp,vibe]"
RUN pip install --no-cache-dir "sentence-transformers>=5.2,<6"
# Mem0 mem-pin protobuf<7 sedangkan Neonize butuh protobuf>=7; protobuf 7 tetap dipakai
# dan Mem0 dipasang tanpa resolusi dependensi, lalu dependensinya dipasang eksplisit.
RUN pip install --no-cache-dir --no-deps "mem0ai>=2.2,<3" \
    && pip install --no-cache-dir httpx openai posthog pydantic pytz qdrant-client protobuf

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin sanka \
    && mkdir -p /app/.sanka \
    && chown -R sanka:sanka /app

# Harus SETELAH useradd: grup docker mengikuti gid socket di host.
ARG DOCKER_GID=994
RUN groupadd -g "${DOCKER_GID}" docker || true \
    && usermod -aG docker sanka

USER sanka
# Model embedder Mem0 diunduh sekali saat build ke cache milik user sanka.
ENV HF_HOME=/app/.sanka/hf
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')"

STOPSIGNAL SIGINT
# `sanka run` membuat schema, men-seed dokumen, lalu acquire lease primary.
CMD ["python", "-m", "sanka", "run"]
