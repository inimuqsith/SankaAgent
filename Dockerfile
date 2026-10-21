FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Jakarta

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg libmagic1 tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY sanka ./sanka
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin sanka \
    && mkdir -p /app/.sanka \
    && chown -R sanka:sanka /app

USER sanka
STOPSIGNAL SIGINT
CMD ["python", "-m", "sanka"]
