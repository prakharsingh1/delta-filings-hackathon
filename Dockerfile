FROM python:3.12-slim

# Poppler/tesseract back the PDF text extraction used by the filing parser.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils tesseract-ocr ghostscript curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Frozen demo: every external endpoint is served from the bundled cache, so the
# container makes no outbound calls and cannot be slowed by NSE, SEBI or wifi.
ENV DELTA_DEMO_FREEZE=1 \
    DELTA_DEMO_MODE=1 \
    DELTA_SKIP_PRELOAD=1 \
    PORT=8080

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://localhost:8080/demo -o /dev/null || exit 1

# 2 workers: the app is IO-light in frozen mode and SQLite is the shared store.
CMD ["sh", "-c", "gunicorn -w 2 -b 0.0.0.0:${PORT} --timeout 120 main:app"]
