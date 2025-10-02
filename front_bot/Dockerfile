FROM python:3.12-slim

WORKDIR /app/front_bot
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY front_bot/requirements.txt /app/front_bot/requirements.txt
RUN pip install --no-cache-dir -r /app/front_bot/requirements.txt

COPY front_bot /app/front_bot

CMD ["python", "main.py"]
