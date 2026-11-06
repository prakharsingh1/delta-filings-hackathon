FROM python:3.12-slim

# Poppler/tesseract back the PDF text extraction used by the filing parser.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils tesseract-ocr ghostscript curl tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# NOTE: demo/freeze flags are deliberately NOT baked in here. Setting
# DELTA_DEMO_FREEZE=1 stops all outbound calls (prices, filings, SEBI go stale)
# and DELTA_DEMO_MODE=1 turns @login_required into a no-op. Set them per-env
# only when you actually want a frozen demo build.
ENV PORT=8080
# Deadlines are Indian dates. On UTC, date.today() runs a day behind from
# midnight to 5:30 am IST (overdue flags, "filed today" rejected as future).
ENV TZ=Asia/Kolkata

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://localhost:8080/ -o /dev/null || exit 1

# One worker keeps memory (the Railway bill) down; four threads stop a slow
# request — a live IBBI scan or a regulator-feed refresh — from blocking
# every other user. SQLite connections are per call, so threads are safe.
CMD ["sh", "-c", "gunicorn -w 1 --worker-class gthread --threads 4 -b 0.0.0.0:${PORT} --timeout 120 main:app"]
