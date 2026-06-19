# Host-agnostic image (works on Render, Fly.io, Railway, Cloud Run, etc.).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV MCP_TRANSPORT=http
EXPOSE 8000
# server.py binds to $PORT if the host sets it, else 8000.
CMD ["python", "server.py"]
