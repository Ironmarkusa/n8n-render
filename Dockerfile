# Start from the official n8n Docker image
FROM n8nio/n8n:latest

# Switch to the root user to install new software
USER root

# Use Alpine's package manager 'apk' to install Python.
# The base python3 package includes the venv module.
RUN apk add --no-cache python3 py3-pip

# Create a virtual environment in /opt/venv
RUN python3 -m venv /opt/venv

# Add the virtual environment's bin directory to the PATH.
# This ensures that commands use the venv's Python and pip.
ENV PATH="/opt/venv/bin:$PATH"

# Copy the requirements file into the container's main directory
COPY requirements.txt .

# Upgrade pip and install the Python libraries into the virtual environment.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# --- FIX: Copy the Python script to a directory in the PATH ---
# This makes the script directly executable as a command.
COPY command_line_scraper.py /usr/local/bin/scraper
RUN chmod +x /usr/local/bin/scraper

# Switch back to the default, non-root user that n8n runs as
USER node
