# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DOC_MCP_TRANSPORT=streamable-http \
    DOC_MCP_HOST=0.0.0.0 \
    DOC_MCP_PORT=8000

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

# ComfyUI MCP server network address (override at runtime).
# The server connects to an already-running ComfyUI MCP server over HTTP/SSE.
ENV COMFY_MCP_URL="" \
    COMFY_MCP_API_KEY="" \
    COMFY_MCP_TRANSPORT=auto

EXPOSE 8000

# Default: serve over streamable-http. Override DOC_MCP_TRANSPORT=stdio
# if you run the container as a stdio MCP server instead.
CMD ["python", "-m", "document_creation_mcp.server"]
