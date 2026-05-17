FROM hqnguyen36/tutora-ai-base:latest
WORKDIR /app
COPY . .
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
