FROM python:3.11-slim

# Node is required by claude-agent-sdk (it drives the Claude Code CLI)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt alpaca-mcp-server

COPY . .

ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]
