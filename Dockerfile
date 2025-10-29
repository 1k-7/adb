FROM python:3.11-slim


ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1


WORKDIR /app


COPY requirements.txt .


RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


COPY . .


RUN mkdir sessions


EXPOSE 8080


CMD gunicorn --bind 0.0.0.0:$PORT main:app
