FROM n8nio/n8n:1-bullseye

USER root

# If base is Debian buster (EOL), point APT to the archive mirrors so apt still works.
# Safe to run even if not buster; it will no-op if the patterns don't match.
RUN set -eux; \
    if grep -qi 'buster' /etc/os-release || grep -qi 'buster' /etc/debian_version || grep -q 'buster' /etc/apt/sources.list; then \
      sed -i 's|deb.debian.org/debian|archive.debian.org/debian|g' /etc/apt/sources.list && \
      sed -i 's|security.debian.org|archive.debian.org|g' /etc/apt/sources.list && \
      sed -i '/buster-updates/d' /etc/apt/sources.list; \
    fi; \
    apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Virtualenv
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" PYTHONUNBUFFERED=1

# Python deps
COPY requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /opt/requirements.txt

# Scripts
RUN mkdir -p /data/scripts
COPY command_line_scraper.py  /data/scripts/command_line_scraper.py
COPY fetch_monthly_metrics.py /data/scripts/fetch_monthly_metrics.py

RUN chown -R node:node /data/scripts && \
    chmod 755 /data/scripts/command_line_scraper.py /data/scripts/fetch_monthly_metrics.py

USER node
