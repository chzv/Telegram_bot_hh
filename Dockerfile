FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app


# для psycopg2 и прочего
RUN apt-get update && apt-get install -y build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# зависимости
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# код
COPY backend /app/backend
COPY adminka /app/adminka



EXPOSE 8000

# запускаем через python -m, чтобы не зависеть от PATH
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
