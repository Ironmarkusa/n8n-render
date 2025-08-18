FROM n8nio/n8n:latest-debian-12
# or: FROM n8nio/n8n:1.73.1-debian-12  (whatever tag your registry supports)

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" PYTHONUNBUFFERED=1

COPY requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /opt/requirements.txt

RUN mkdir -p /data/scripts
COPY command_line_scraper.py  /data/scripts/command_line_scraper.py
COPY fetch_monthly_metrics.py /data/scripts/fetch_monthly_metrics.py

RUN chown -R node:node /data/scripts && \
    chmod 755 /data/scripts/command_line_scraper.py /data/scripts/fetch_monthly_metrics.py

USER node
