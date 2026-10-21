FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Jakarta

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg libmagic1 tzdata \
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
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin sanka \
    && mkdir -p /app/.sanka \
    && chown -R sanka:sanka /app

# Harus SETELAH useradd: grup docker mengikuti gid socket di host. Cek dengan
# `stat -c %g /var/run/docker.sock` di host (sekarang 994); timpa lewat
# `--build-arg DOCKER_GID=<gid>` bila berbeda.
ARG DOCKER_GID=994
RUN groupadd -g "${DOCKER_GID}" docker || true \
    && usermod -aG docker sanka

USER sanka
STOPSIGNAL SIGINT
CMD ["python", "-m", "sanka"]
