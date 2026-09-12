FROM python:3.12-slim AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY app/requirements.txt app/requirements.txt
RUN pip install --no-cache-dir \
        https://github.com/KurimuzonAkuma/pyrogram/archive/dev.zip \
    && pip install --no-cache-dir --no-deps pytgcalls pyromod \
    && pip install --no-cache-dir -r app/requirements.txt

COPY app/ app/
COPY docs/ docs/

RUN mkdir -p app/downloads app/sessions app/logs

ENV PYTHONPATH=/workspace

CMD ["python", "-m", "app.main"]
