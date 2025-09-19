# OpenSubtitles MCP Server

A simple MCP server using FastMCP that integrates with OpenSubtitles REST API.

## Setup

1. Create and activate a virtualenv

2. Install deps

3. Configure environment

- Create a .env file with:

| Variable                | Description                                                     | Default   |
| ----------------------- | --------------------------------------------------------------- | --------- |
| OPEN_SUBTITLES_API_KEY  | OpenSubtitles API key used for authenticating REST requests.    | (none)    |
| OPEN_SUBTITLES_TOKEN    | Optional bearer token for /download Authorization header.       | (none)    |
| OPEN_SUBTITLES_USERNAME | Optional username to fetch token if OPEN_SUBTITLES_TOKEN unset. | (none)    |
| OPEN_SUBTITLES_PASSWORD | Optional password to fetch token if OPEN_SUBTITLES_TOKEN unset. | (none)    |
| MCP_HTTP_HOST           | Host interface for Streamable HTTP transport.                   | 127.0.0.1 |
| MCP_HTTP_PORT           | Port for Streamable HTTP transport.                             | 8000      |
| MCP_HTTP_PATH           | URL path for Streamable HTTP transport.                         | /mcp      |

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

## Tools

- download_subtitles(movie_name): returns subtitle text for a movie
- verify_movie(name): returns boolean whether the name is a movie
