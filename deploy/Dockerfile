# photo-sorter web app for Cloud Run (Vertex AI embeddings, no model weights inside).
# Each user signs in with Google and pastes the link of a Drive folder; the categories and settings live in that
# folder (CATEGORIES.md, .sorter/settings.json), so the image carries no config.
# Build locally:  docker build -f deploy/Dockerfile -t photo-sorter .
# For a VPS with the local SigLIP model instead, build with:  --build-arg EXTRAS=researcher  (and PHOTO_SORTER_PROVIDER=siglip)
FROM python:3.12-slim
ARG EXTRAS=cloud
ENV PYTHONUNBUFFERED=1 UV_SYSTEM_PYTHON=1 UV_COMPILE_BYTECODE=1 \
    PHOTO_SORTER_HOSTED=1 HF_HOME=/data/hf PORT=8080
RUN apt-get update && apt-get install -y --no-install-recommends libheif1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY photo_sorter ./photo_sorter
RUN uv pip install --no-cache ".[${EXTRAS}]"
EXPOSE 8080
CMD ["sh", "-c", "uvicorn photo_sorter.web.app:app_from_env --factory --host 0.0.0.0 --port ${PORT}"]
