# Образ веба и синхронизации SalesVisor. База закреплена (bookworm): в контуре «Инкаб ИИ» она уже лежит на ai-ag,
# а Docker Hub оттуда временами отвечает 429. Другую базу можно передать: --build-arg BASE_IMAGE=...
ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 TZ=Asia/Yekaterinburg
# tzdata — чтобы TZ=Asia/Yekaterinburg действовал («План на день» считает сегодняшнюю дату по местному времени).
# Если apt недоступен, сборка не падает: тогда в .env задать TZ=<+05>-5 (в Екатеринбурге нет летнего времени)
RUN (apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*) \
 || echo "ВНИМАНИЕ: tzdata не установлен — задайте TZ=<+05>-5 в .env"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY salesvisor ./salesvisor
EXPOSE 8000
CMD ["python", "-m", "salesvisor", "serve", "--port", "8000"]
