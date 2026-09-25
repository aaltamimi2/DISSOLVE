# The hosted DISSOLVE web app on a small CPU instance (dissolve web). Literature search, live TEA and PDF ingest
# need more memory than such a host has (1.8 GB for the literature models, 0.9 GB per TEA run), so this image
# leaves their stack out and the app switches them off (DISSOLVE_WEB_DISABLE). ./dissolve runs everything locally.
FROM python:3.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# The locked environment without torch, Docling, sentence-transformers and what only they need.
RUN uv export --locked --no-dev --no-emit-project --prune torch --prune docling --prune sentence-transformers \
        --prune transformers --prune huggingface-hub --prune langchain --prune langchain-openai --prune pypdf \
        > /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && uv pip install --system --no-cache "psycopg[binary]==3.3.6"
# psycopg: the hosted app keeps accounts and chats in Postgres (web_accounts.py), so only this image needs a driver.
COPY src ./src
RUN uv pip install --system --no-cache --no-deps . && useradd --create-home dissolve
USER dissolve
ENV DISSOLVE_WEB_DISABLE=literature,tea PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["dissolve", "web", "--host", "0.0.0.0", "--port", "8080"]
