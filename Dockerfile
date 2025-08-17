# Start from the official n8n Docker image
FROM n8nio/n8n:latest

# Switch to the root user to install new software
USER root

# Use Alpine's package manager 'apk' to install Python and the venv module.
# The correct package name for the venv module is 'py3-venv'.
RUN apk add --no-cache python3 py3-pip py3-venv

# Create a virtual environment in /opt/venv
RUN python3 -m venv /opt/venv

# Add the virtual environment's bin directory to the PATH.
# This ensures that 'python' and 'pip' commands use the venv's versions.
ENV PATH="/opt/venv/bin:$PATH"

# Copy the requirements file into the container's main directory
COPY requirements.txt .

# Install the Python libraries from your requirements file into the virtual environment.
# The --no-cache-dir flag is used to keep the image size down.
RUN pip install --no-cache-dir -r requirements.txt

# Switch back to the default, non-root user that n8n runs as
USER node
