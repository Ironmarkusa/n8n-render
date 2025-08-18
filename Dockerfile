# Use the Debian flavor so apt-get exists and psycopg2-binary wheels work
FROM n8nio/n8n:latest-debian

# Become root to install system packages
USER root

# System Python + venv tools + CA certs
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Create a dedicated virtualenv for your scripts
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# Install Python deps
COPY requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /opt/requirements.txt

# Place scripts in a predictable path
RUN mkdir -p /data/scripts
COPY command_line_scraper.py    /data/scripts/command_line_scraper.py
COPY fetch_monthly_metrics.py   /data/scripts/fetch_monthly_metrics.py

# Make them executable and owned by the n8n user
RUN chown -R node:node /data/scripts && \
    chmod 755 /data/scripts/command_line_scraper.py /data/scripts/fetch_monthly_metrics.py

# Run the container as the standard n8n user
USER node
