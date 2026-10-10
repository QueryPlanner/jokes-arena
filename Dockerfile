FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ARENA_DATABASE=/data/arena.db
ARG REVISION=development
ENV ARENA_REVISION=$REVISION
LABEL org.opencontainers.image.source="https://github.com/QueryPlanner/jokes-arena" \
      org.opencontainers.image.revision=$REVISION
RUN groupadd --gid 1000 arena && useradd --uid 1000 --gid 1000 --no-create-home arena
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY arena/ arena/
COPY config/ config/
COPY preview/index.html preview/styles.css preview/app.js preview/
USER 1000:1000
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--worker-class", "sync", "--timeout", "15", "--graceful-timeout", "15", "--max-requests", "1000", "--max-requests-jitter", "100", "--access-logfile", "/dev/null", "arena.server:create_app()"]
