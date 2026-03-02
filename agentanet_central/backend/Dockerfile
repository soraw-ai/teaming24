# AgentaNet Central Service - Backend Dockerfile

FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 8080

# Run server
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8080"]
