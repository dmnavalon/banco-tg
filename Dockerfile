FROM python:3.13-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium deps manually with Debian package names.
# playwright --with-deps falls back to Ubuntu 20.04 package names (ttf-unifont,
# ttf-ubuntu-font-family) which don't exist on Debian bookworm/trixie.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-liberation fonts-unifont \
        libnss3 libnspr4 libdbus-1-3 \
        libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libdrm2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 \
        libx11-6 libx11-xcb1 libxcb1 libxext6 \
    && (apt-get install -y --no-install-recommends libasound2 || apt-get install -y --no-install-recommends libasound2t64) \
    && rm -rf /var/lib/apt/lists/*
RUN playwright install chromium

COPY . .

ENV HEADLESS=true
ENV LOG_LEVEL=INFO

CMD ["python", "-m", "src.bot"]
