FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    "fastapi>=0.110,<1" \
    "uvicorn[standard]>=0.29,<1" \
    "pydantic>=2.6,<3" \
    "pydantic-settings>=2.2,<3" \
    "psycopg[binary]>=3.2,<4" \
    "pymongo>=4.6,<5"
COPY app ./app
COPY db ./db
COPY scripts ./scripts

RUN useradd -r -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8097
HEALTHCHECK --interval=10s --timeout=3s CMD python -c \
  "import urllib.request; urllib.request.urlopen('http://localhost:8097/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8097"]
