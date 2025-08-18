FROM n8nio/n8n:latest

# --- System Python on Debian-based n8n images ---
USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# --- Virtualenv ---
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# --- Python deps for BOTH scripts ---
# requests, urllib3, pydantic, beautifulsoup4, html2text, openai, psycopg2-binary (for DB access if needed)
# add anything else you use
COPY requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /opt/requirements.txt

# --- Put scripts in a predictable place ---
RUN mkdir -p /data/scripts
# Your existing scraper
COPY command_line_scraper.py /data/scripts/command_line_scraper.py
# Your new GA/GSC fetcher (name it as you like)
COPY fetch_monthly_metrics.py /data/scripts/fetch_monthly_metrics.py

# Make them executable and owned by the n8n user
RUN chown -R node:node /data/scripts && \
    chmod 755 /data/scripts/command_line_scraper.py /data/scripts/fetch_monthly_metrics.py

USER node
