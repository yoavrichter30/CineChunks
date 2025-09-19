# CineChunks Web

Simple web UI + FastAPI server.

## Run

```bash
pip3.12 install -r requirements.txt
python3.12 -m uvicorn app:app --reload --port 505
```

## Environment

| Variable       | Description                              | Default                   |
| -------------- | ---------------------------------------- | ------------------------- |
| OPENAI_API_KEY | OpenAI API key                           | (none)                    |
| MCP_URL        | MCP server Streamable HTTP endpoint      | http://127.0.0.1:8000/mcp |
| OPENAI_MODEL   | OpenAI model to use for chat completions | gpt-4o-mini               |
