FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# SQLite file will live here (mounted as an emptyDir/volume in k8s later)
RUN mkdir -p /data

EXPOSE 5000

# Use gunicorn (production-grade WSGI server) instead of Flask's dev server.
# --workers 2: two worker processes handling requests inside each pod.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:app"]
