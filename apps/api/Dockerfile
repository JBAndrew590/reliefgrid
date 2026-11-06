FROM python:3.12-slim AS runtime
WORKDIR /app
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY reliefgrid ./reliefgrid
RUN pip install --no-cache-dir --no-deps .
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
RUN useradd -u 10001 -r appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "reliefgrid.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test
USER root
RUN pip install --no-cache-dir -e '.[dev]'
USER appuser
