# Use a specific, recent, Debian-based version of n8n
FROM n8nio/n8n:1.45.1-debian

# Switch to the root user to install packages
USER root

# This block is for older Debian versions, but is safe to keep.
# On this newer image, the 'if' condition will simply be false and the script will continue.
RUN set -eux; \
    if grep -qi 'buster' /etc/os-release || grep -qi 'buster' /etc/debian_version || grep -q 'buster' /etc/apt/sources.list; then \
      sed -i 's|deb.debian.org/debian|archive.debian.org/debian|g' /etc/apt/sources.list && \
      sed -i 's|security.debian.org|archive.debian.org|g' /etc/apt/sources.list && \
      sed -i '/buster-updates/d' /etc/apt/sources.list; \
    fi; \
    # This will now succeed because the image is Debian-based
    apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Create and activate the Python virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" PYTHONUNBUFFERED=1

# Install Python dependencies
COPY requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /opt/requirements.txt

# Copy your custom scripts into the container
RUN mkdir -p /data/scripts
COPY command_line_scraper.py  /data/scripts/command_line_scraper.py
COPY fetch_monthly_metrics.py /data/scripts/fetch_monthly_metrics.py

# Set correct ownership and permissions for the n8n user
RUN chown -R node:node /data/scripts && \
    chmod 755 /data/scripts/command_line_scraper.py /data/scripts/fetch_monthly_metrics.py

# Switch back to the non-root 'node' user to run n8n
USER node
