# The hosted DISSOLVE web app (dissolve web) on a 2 GB, one-CPU instance. Literature search serves BGE hybrid (BM25 and
# BGE-base vectors, z-score fusion) without the bge-reranker-base pass: measured 2026-09-28 in this image on one CPU,
# a search takes 0.26 s and adds 0.8 GB, where the reranker would add 14 s and 1.3 GB. Live TEA (0.9 GB per run) and
# PDF ingest (Docling) stay out, and the app switches TEA off (DISSOLVE_WEB_DISABLE). ./dissolve runs everything locally.
FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv
# RDKit's drawing module (the structures in answer tables) needs the X render libraries, which a slim image leaves
# out: without them the live site answered "libXrender.so.1: cannot open shared object file" (2026-09-25).
RUN apt-get update && apt-get install -y --no-install-recommends libxrender1 libxext6 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
# torch comes first, from PyTorch's CPU index: uv.lock pins the CUDA 12.8 build for Linux, which would put several GB
# of NVIDIA libraries in an image that has no GPU. The rest of the lock is then installed exactly as pinned, without
# re-resolving (torch is already there), minus Docling and the stacks only the CLI uses.
RUN uv pip install --system --no-cache --index-url https://download.pytorch.org/whl/cpu torch==2.10.0 \
    && uv export --locked --no-dev --no-emit-project --prune torch --prune docling --prune langchain \
        --prune langchain-openai --prune pypdf > /tmp/requirements.txt \
    && uv pip install --system --no-cache --no-deps -r /tmp/requirements.txt \
    && uv pip install --system --no-cache "psycopg[binary]==3.3.6"
# psycopg: the hosted app keeps accounts and chats in Postgres (web_accounts.py), so only this image needs a driver.
COPY src ./src
RUN uv pip install --system --no-cache --no-deps . && useradd --create-home dissolve
# The pinned BGE query encoder, fetched at build time so no search waits on a download; offline at run time.
ENV HF_HOME=/opt/hf
RUN python -c "from dissolve import research; research._dense_vectors(['warm'])" && chmod -R a+rX /opt/hf
USER dissolve
# One torch thread: the container sees every host CPU, and a dozen threads under a one-CPU quota took a search from
# 0.26 s to 1.5 s.
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=1 DISSOLVE_PAIR_RERANK=off \
    DISSOLVE_WEB_DISABLE=tea PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["dissolve", "web", "--host", "0.0.0.0", "--port", "8080"]
