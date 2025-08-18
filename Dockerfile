# Start from the official n8n Docker image (Alpine version)
FROM n8nio/n8n:latest

# Switch to the root user to install new software
USER root

# Use Alpine's package manager 'apk' to install Python and pip
RUN apk add --no-cache python3 py3-pip

# Create and activate a Python virtual environment
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy the requirements file into the container's working directory
COPY requirements.txt .

# Install the Python dependencies from the requirements file
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# --- FIX ---
# Copy BOTH scripts to the default working directory
# On Render, this is /opt/render/project/src/
COPY command_line_scraper.py .
COPY fetch_monthly_metrics.py .

# Switch back to the default, non-root user that n8n runs as
USER node
