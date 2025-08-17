# Start from the official n8n Docker image
FROM n8nio/n8n:latest

# Switch to the root user to install new software
USER root

# Use Alpine's package manager 'apk' to install Python and pip.
# The '--no-cache' flag helps keep the image size smaller.
RUN apk add --no-cache python3 py3-pip

# Copy the requirements file into the container's main directory
COPY requirements.txt .

# Install the Python libraries from your requirements file
RUN pip3 install --no-cache-dir -r requirements.txt

# Switch back to the default, non-root user that n8n runs as
USER node
