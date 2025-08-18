# Start from the correct, modern, Debian-based n8n image
FROM n8n/n8n:1.45.1

# Switch to the root user to install packages
USER root

# Install Python and related tools using Debian's package manager
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-venv python3-pip && \
    rm -rf /var/lib/apt/lists/*

# Create and activate a Python virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy the requirements file to the default working directory
COPY requirements.txt .

# Install the Python dependencies from the requirements file
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# --- THE FIX ---
# Copy BOTH scripts to the default working directory where n8n can find them easily.
COPY command_line_scraper.py .
COPY fetch_monthly_metrics.py .

# Switch back to the non-root 'node' user to run n8n
USER node
