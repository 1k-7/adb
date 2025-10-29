# Use an official lightweight Python image
FROM python:3.11-slim

# Install supervisor and OS dependencies first
RUN apt-get update && apt-get install -y supervisor

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file first for layer caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Create the directory for session files
RUN mkdir sessions

# Create user and set permissions
RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup --no-create-home appuser && \
    chown -R appuser:appgroup /app && \
    chmod -R u+rwx /app
USER appuser

# Copy supervisor config into the container
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose the port Koyeb will use for the web service
EXPOSE 8080

# Run supervisor. This will now be the main process.
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
