FROM python:3.13-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY . .

ENV HEADLESS=true
ENV LOG_LEVEL=INFO

CMD ["python", "-m", "src.bot"]
