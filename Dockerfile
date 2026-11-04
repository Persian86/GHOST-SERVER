FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GHOST_DATA=/data GHOST_PORT=8780 TZ=Australia/Melbourne
WORKDIR /app
# ffmpeg turns RTSP security cameras into a browser-friendly live stream
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ghost_server ./ghost_server
COPY sdk ./sdk
VOLUME ["/data"]
EXPOSE 8780
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8780/api/v1/health', timeout=4)" || exit 1
CMD ["python", "-m", "ghost_server.app"]
