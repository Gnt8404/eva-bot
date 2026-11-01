# Ева — Telegram-бот-помощник сервера Aetherlyn.
#
# Один контейнер: бот сам перезапускает себя при падении (--loop), данные и
# логи лежат в томе /data. Секреты лучше передавать переменными окружения
# (RM_AI_BOT_TOKEN, RM_AI_DEEPSEEK_KEY), а не файлом .env.
#
#   docker build -t eva-bot .
#   docker run -d --name eva --restart always \
#       -e RM_AI_BOT_TOKEN=... -e RM_AI_DEEPSEEK_KEY=... \
#       -v eva-data:/data eva-bot
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    BOT_DATA_DIR=/data

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN mkdir -p /data
VOLUME ["/data"]

CMD ["python", "-u", "bot.py", "--loop"]
