FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl \
  && rm -rf /var/lib/apt/lists/*

# Install the lightweight worker dependencies separately so governance and queue
# checks can run without downloading the vision/ML weights or making model calls.
COPY pyproject.toml ./
RUN mkdir -p pipeline && touch pipeline/__init__.py \
 && pip install --upgrade pip && pip install -e .
COPY . .

FROM base AS runtime
# torch + torchvision are a MATCHING pair
# from the pytorch CPU index, installed BEFORE .[ml] — otherwise pip pulls a
# torchvision built against a different torch and CLIP dies with
# "operator torchvision::nms does not exist".
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.13.0" torchvision \
 && pip install -e ".[ml]"

CMD ["arq", "pipeline.worker.WorkerSettings"]
