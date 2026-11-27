FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Runs as a non-root user; the code stays root-owned so the app can't rewrite it.
USER app
# Into the app user's home: reads Overture's files over HTTPS.
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')"

# a+rX: still readable by the app user when the checkout's files are owner-only (umask 077).
COPY --chmod=a+rX alembic.ini ./
COPY --chmod=a+rX migrations ./migrations
COPY --chmod=a+rX app ./app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]


# Tests and pytest live only here: docker compose run --rm --build tests
FROM base AS test
USER root
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
USER app
COPY --chmod=a+rX pytest.ini ./
COPY --chmod=a+rX tests ./tests
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--ignore=tests/evals"]


# The default build (docker compose): no tests, no pytest.
FROM base AS runtime
