FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements-dev.lock pyproject.toml ./
RUN python -m pip install --no-cache-dir -r requirements-dev.lock

COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps .

CMD ["sh", "-c", "exec uvicorn shape_finder.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
