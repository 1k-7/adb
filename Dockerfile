# Use an official lightweight Python image
FROM python:3.11-slim

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

#
# --- NEW ---
# Create a non-root user and give it permissions
RUN adduser -D -g '' appuser && chown -R appuser:appuser /app
# Switch to this new user
USER appuser
# --- END NEW ---

# Make the start script executable
RUN chmod +x ./start.sh

# Expose the port Koyeb will use for the web service
EXPOSE 8080

# Set the new start script as the main command
CMD ["./start.sh"]
