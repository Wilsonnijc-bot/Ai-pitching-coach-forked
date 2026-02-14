FROM python:3.11-slim

# System deps: ffmpeg for audio/video conversion, libgl1+libglib for opencv
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    # mediapipe pulls in opencv-contrib-python (GUI variant) as a dep;
    # swap it for the headless variant to avoid X11/Mesa deps.
    pip uninstall -y opencv-contrib-python 2>/dev/null; \
    pip install --no-cache-dir --no-deps opencv-contrib-python-headless && \
    # Remove jax/jaxlib — only used by mediapipe Model Maker, not by
    # legacy Pose/FaceMesh that we use. Saves ~200MB.
    pip uninstall -y jax jaxlib ml-dtypes opt-einsum 2>/dev/null; \
    true

COPY . .

# Cloud Run sets $PORT (default 8080)
ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
