# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies
# git is required for GitTool
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the engine code
COPY . .

# Expose the backend port
EXPOSE 8002

# Run the application
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8002"]
