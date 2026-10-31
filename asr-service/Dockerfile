FROM python:3.12-slim
RUN pip install --no-cache-dir aiohttp==3.10.11 vosk==0.3.45
WORKDIR /app
COPY server.py /app/server.py
EXPOSE 8082
# init через docker --init (PID1-reaper) задаётся в compose
CMD ["python", "server.py"]
