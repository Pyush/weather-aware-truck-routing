# Backend image.
#
# Multi-stage so the runtime image carries no build toolchain: the wheels are
# built in the builder and only the resulting site-packages are copied forward.
# Runs as a non-root user -- the application needs no write access to its own
# code, so nothing should have it.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Compilers are needed for any dependency without a wheel; they do not reach the
# final image.
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copied before the source so the dependency layer is reused whenever only
# application code changes.
COPY pyproject.toml ./
COPY tripplan ./tripplan
COPY config ./config
COPY manage.py ./

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod

RUN groupadd --system app && useradd --system --gid app --home /app app
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app config ./config
COPY --chown=app:app tripplan ./tripplan
COPY --chown=app:app manage.py ./

# gunicorn writes a control socket/state file into the working directory. As a
# non-root user with a read-only /app it fails with "Permission denied:
# /app/.gunicorn" on every start, so give the process a directory it owns.
RUN mkdir -p /app/run && chown app:app /app/run
ENV GUNICORN_CTL_DIR=/app/run

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/', timeout=4).status == 200 else 1)"

# Threads, not workers: the endpoint is IO-bound on upstream HTTP calls, so a
# single process with a thread pool uses the memory far better than N workers,
# and the process cache stays warm instead of being duplicated per worker.
# Timeout < 30s so a stuck upstream is killed and restarted before the load
# balancer gives up on the request.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "90", \
     "--graceful-timeout", "30", \
     "--worker-tmp-dir", "/app/run", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]