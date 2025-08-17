# Start from the official n8n Docker image
FROM n8nio/n8n:latest

# Switch to the root user to install new software
USER root

# Update the system and install Python and pip.
# The `&& rm -rf /var/lib/apt/lists/*` part helps keep the image size smaller.
RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container's main directory
COPY requirements.txt .

# Install the Python libraries from your requirements file
RUN pip install --no-cache-dir -r requirements.txt

# Switch back to the default, non-root user that n8n runs as
USER node
