FROM python:3.10-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg git curl build-essential gcc g++ \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "pip<25" setuptools wheel cython "numpy<2"
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    torch==2.3.1 torchaudio==2.3.1
RUN pip install --no-cache-dir pyworld==0.3.4
RUN pip install --no-cache-dir rvc-python==0.1.5
RUN pip install --no-cache-dir aiohttp==3.10.11
WORKDIR /app
COPY server.py /app/
EXPOSE 8081
CMD ["python", "server.py"]
