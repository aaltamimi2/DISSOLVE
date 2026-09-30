# The hosted DISSOLVE web app (dissolve web) with live TEA, on a 2 GB, one-CPU instance. The owner's condition
# (2026-09-29) is that it stays under 2 GB in any scenario; the budget is measured and enforced as follows.
# - Literature search serves BGE hybrid with the pinned int8 ONNX query encoder, and the image has no torch, so no code
#   path can load one.
# - One live TEA worker runs at a time, under a kernel memory limit, and dies with its caller
#   (deploy/tea-python-guarded).
# - At most DISSOLVE_MAX_TURNS answers run at once (a TEA panel frees its answer's place while it waits or runs), at
#   most four times that many are in progress at all, and a request carries at most 2 MB (web.py).
# - Each DuckDB database is capped (DISSOLVE_DUCKDB_MEMORY_LIMIT), and chats left idle leave memory.
# Measured in this image on one CPU and 2 GB (2026-09-29): a TEA worker peaks at 845 MB whatever the plant (934 MB when
# numba's cache is empty) and the app at 611 MB. Two TEA sweeps, 12 chained literature questions, two separation plans
# on 7 polymers and 40 more chats at once peaked at 1,261 MB for the whole container.
# PDF ingest (Docling) stays out. ./dissolve runs everything locally.

# The plastics process model, in a stage of its own, so that only this stage sees the build-time token.
FROM python:3.11-slim-bookworm AS plastics
ARG PLASTICS_TOKEN
ARG PLASTICS_CORE_COMMIT=e7575c38070335a6b63872fe30f82d778ac6d2bc
COPY deploy/ /ctx/
RUN python /ctx/fetch_plastics.py /plastics

FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv
# RDKit's drawing module (the structures in answer tables) needs the X render libraries, which a slim image leaves
# out: without them the live site answered "libXrender.so.1: cannot open shared object file" (2026-09-25).
RUN apt-get update && apt-get install -y --no-install-recommends libxrender1 libxext6 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# The live TEA worker: Python 3.12 with exactly requirements-tea.txt, the environment ./dissolve builds as .venv-tea.
# Bytecode is compiled here: the worker runs as a user who cannot write it, and every run would compile BioSTEAM again.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
COPY requirements-tea.txt ./
RUN uv venv /opt/tea --python 3.12.13 \
    && uv pip install --python /opt/tea/bin/python --no-cache --compile-bytecode -r requirements-tea.txt
# The web app: uv.lock exactly as pinned, without torch and what only needs it (the torch encoder, the reranker and
# Docling's PDF models), and without the stacks only the CLI uses. ONNX Runtime and the tokenizer run the query
# encoder; psycopg is the hosted app's database driver (accounts and chats live in Postgres, web_accounts.py).
COPY pyproject.toml uv.lock ./
RUN uv export --locked --no-dev --no-emit-project --prune torch --prune sentence-transformers --prune transformers \
        --prune docling --prune langchain --prune langchain-openai --prune pypdf > /tmp/requirements.txt \
    && uv pip install --system --no-cache --compile-bytecode --no-deps -r /tmp/requirements.txt \
    && uv pip install --system --no-cache --compile-bytecode -c /tmp/requirements.txt onnxruntime==1.23.2 \
        tokenizers==0.22.2 "psycopg[binary]==3.3.6"
# The query encoder, made at build time so no search waits on a download (deploy/prepare_models.py). It runs in a
# throwaway environment with the versions that give the pinned bytes.
ENV HF_HOME=/opt/hf
COPY deploy/prepare_models.py src/dissolve/research.py /tmp/models/
RUN uv venv /tmp/quant --python /usr/local/bin/python3.11 \
    && uv pip install --python /tmp/quant/bin/python --no-cache onnx==1.20.1 onnxruntime==1.23.2 numpy==2.4.6 \
        huggingface-hub==0.36.0 \
    && /tmp/quant/bin/python /tmp/models/prepare_models.py /tmp/models/research.py /opt/models \
    && rm -rf /tmp/quant /tmp/models && chmod -R a+rX /opt/hf /opt/models
# An editable install keeps the package at /app/src, the root tea.py hands the worker: installed into site-packages,
# it would put this Python 3.11 environment on the Python 3.12 worker's path.
COPY src ./src
RUN uv pip install --system --no-cache --no-deps -e . && python -m compileall -q src && useradd --create-home dissolve
COPY --from=plastics /plastics /opt/plastics
COPY deploy/tea-python-guarded /usr/local/bin/
COPY deploy/check_live_tea.py /opt/
RUN chmod 755 /usr/local/bin/tea-python-guarded && mkdir -p /opt/tea-cache && chown dissolve /opt/tea-cache
# One thread for every numerical library: the container sees every host CPU, and a dozen threads under a one-CPU quota
# took a search from 0.26 s to 1.5 s. Two malloc arenas keep threads from each holding memory of their own.
# numba compiles for a generic x86-64 CPU. It keys its cache on the CPU, and the machine that builds the image
# (icelake-server) is not the one the site runs on (skylake-avx512, 2026-09-30). With that key, every live run on the
# site recompiled BioSTEAM's numba code, took 60 s and passed 1,000 MB, so the guard stopped it.
ENV DISSOLVE_PLASTICS_PATH=/opt/plastics DISSOLVE_TEA_PYTHON=/usr/local/bin/tea-python-guarded \
    DISSOLVE_TEA_MAX_MB=1000 DISSOLVE_TEA_MAX_DATA_MB=950 DISSOLVE_TEA_LOG=/proc/1/fd/1 \
    NUMBA_CACHE_DIR=/opt/tea-cache/numba NUMBA_CPU_NAME=generic MPLCONFIGDIR=/opt/tea-cache/matplotlib \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMBA_NUM_THREADS=1 MALLOC_ARENA_MAX=2 PYTHONUNBUFFERED=1
USER dissolve
# Live TEA reproduces the stored results exactly in this image, and numba's cache is filled, so the site's first run
# compiles nothing.
RUN python /opt/check_live_tea.py
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DISSOLVE_PAIR_RERANK=off \
    DISSOLVE_BGE_ONNX_INT8=/opt/models/bge-base.int8.onnx DISSOLVE_MAX_TURNS=8 DISSOLVE_DUCKDB_MEMORY_LIMIT=256MB
EXPOSE 8080
CMD ["dissolve", "web", "--host", "0.0.0.0", "--port", "8080"]
