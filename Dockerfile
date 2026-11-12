# syntax=docker/dockerfile:1
#
# TalentIQ production image.
#
# Design notes
# - python:3.11-slim, matching the interpreter the test suite is verified on.
#   3.11 is also the floor for `tomllib`, which core/startup_check.py uses to
#   validate .streamlit/config.toml.
# - Dependencies live in a virtualenv at /opt/venv and are copied wholesale from
#   the build stage, so console scripts and package data files both survive.
# - torch is installed CPU-only from the PyTorch CPU index first. The default
#   PyPI torch wheel pulls ~2 GB of CUDA libraries that a Streamlit app never
#   uses; the CPU wheel is a fraction of that. Biggest single lever on size.
# - The embedding model is baked in at build time, as the talentiq user, into
#   HF_HOME. The application loads sentence-transformers with
#   local_files_only=True, so a model absent from the cache is never fetched at
#   runtime — without this step the container starts and looks healthy but has
#   silently lost the semantic JD signal. Build with --build-arg
#   PREFETCH_MODEL=false to skip it and accept the TF-IDF fallback.
#
# Build:
#   docker build -t talentiq .
# Run (secrets come from the environment, never the image):
#   docker run -p 8501:8501 -v talentiq-data:/app/data --env-file .env talentiq

# ---------------------------------------------------------------------------
# Stage 1 — dependencies. Separate from the runtime stage so editing
# application code does not invalidate the slow torch install.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS deps

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# CPU-only torch, installed before requirements.txt so pip never resolves the
# CUDA-enabled default from PyPI.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.10.0"

COPY requirements.txt ./
RUN pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2 — runtime. No compilers, no git, no curl: the shipped attack surface
# is the interpreter plus site-packages.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ARG PREFETCH_MODEL=true
ARG EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    TALENTIQ_HOME=/home/talentiq \
    HOME=/home/talentiq \
    HF_HOME=/home/talentiq/.cache/huggingface \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none

# Container hosts have a small inotify budget and polling would burn CPU to
# achieve nothing, since runOnSave is already false in .streamlit/config.toml.
# Websocket compression is the real lever for payload size over a WAN.
ENV STREAMLIT_SERVER_ENABLE_WEBSOCKET_COMPRESSION=true

RUN groupadd --gid 10001 talentiq \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/talentiq talentiq

COPY --from=deps /opt/venv /opt/venv

WORKDIR /app

# .dockerignore keeps secrets, venv, databases, tests and docs out of this
# layer, so only what is needed to serve the app is copied.
COPY --chown=talentiq:talentiq . .

# The only path the app writes to: SQLite database, temp files, uploads.
RUN mkdir -p /app/data/tmp /app/data/uploads \
 && chown -R talentiq:talentiq /app/data /home/talentiq

USER talentiq

# Runs as talentiq so the cache lands in talentiq-owned HF_HOME. Done as root it
# would be written to /root/.cache and be unreadable at runtime.
RUN if [ "$PREFETCH_MODEL" = "true" ]; then \
        python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')"; \
    else \
        echo "WARNING: PREFETCH_MODEL=false - semantic matching will fall back to skill + TF-IDF scoring."; \
    fi

EXPOSE 8501

# Streamlit's built-in liveness endpoint. Confirms the server process is
# serving; it does not validate configuration. That is a separate, explicit
# step: `python -m core.startup_check`.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status == 200 else 1)"

# Fail closed, in order:
#   1. create/migrate the database now, so a broken volume is reported at deploy
#      time. Streamlit does not execute app.py until a browser connects, so
#      without this the schema would not exist until the first real user
#      arrived. On failure the traceback goes to the container log, which is
#      operator-facing, and the container exits non-zero.
#   2. validate configuration, directories, required data and the model.
#   3. only then serve.
CMD ["sh", "-c", "python -c 'from core.db import init_db; init_db()' && python -m core.startup_check && exec streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0"]
