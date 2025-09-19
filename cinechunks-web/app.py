import os
import json
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional, Any, Dict
import prompts as prompts


app = FastAPI(title="CineChunks Web")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
async def startup_connect_mcp() -> None:
	"""Connect to MCP server on startup and cache tool definitions."""
	mcp_url = os.getenv("MCP_URL", "http://127.0.0.1:8000/mcp").strip()
	try:
		from fastmcp.client import Client
		from fastmcp.client.transports import StreamableHttpTransport
		transport = StreamableHttpTransport(url=mcp_url)
		client = Client(transport)
		app.state.mcp_client = client
		app.state.mcp_tools = []
		# establish connection and prefetch tools
		async with client:
			get_defs = getattr(client, "getToolDefinitions", None)
			if callable(get_defs):
				app.state.mcp_tools = get_defs()
	except Exception:
		# leave state unset if connection fails; background task will fallback
		app.state.mcp_client = None
		app.state.mcp_tools = []


@app.on_event("shutdown")
async def shutdown_disconnect_mcp() -> None:
	client = getattr(app.state, "mcp_client", None)
	if client is not None:
		try:
			await client.aclose()
		except Exception:
			pass


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
	return templates.TemplateResponse("index.html", {"request": request, "result": None})




@app.post("/submit")
async def submit(
    request: Request,
    movie_name: str = Form(...),
    episodes: Optional[str] = Form(default=None),
    episode_length: Optional[str] = Form(default=None),
):
    # Coerce optional numeric inputs (blank strings -> None)
    ep_val: Optional[int] = None
    ep_len_val: Optional[int] = None
    if episodes and str(episodes).strip():
        try:
            ep_val = int(str(episodes).strip())
        except Exception:
            ep_val = None
    if episode_length and str(episode_length).strip():
        try:
            ep_len_val = int(str(episode_length).strip())
        except Exception:
            ep_len_val = None

    user_query = prompts.build_user_prompt(movie_name, ep_val, ep_len_val)
    result = await ask_chatgpt_via_mcp(user_query)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "result": result, "movie_name": movie_name},
    )


async def ask_chatgpt_via_mcp(user_query: str) -> Optional[Dict[str, Any]]:
	"""
	Fire-and-forget placeholder that asks ChatGPT with MCP tools available.
	- Reads OPENAI_API_KEY and optional OPENAI_MODEL.
	- Connects to MCP server over HTTP at MCP_URL (default http://127.0.0.1:8000/mcp).
	- Intentionally does not persist response yet.
	"""
	openai_key = os.getenv("OPENAI_API_KEY", "").strip()
	mcp_url = os.getenv("MCP_URL", "http://127.0.0.1:8000/mcp").strip()
	model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
	if not openai_key:
		return None

	try:
		from openai import AsyncOpenAI
		from fastmcp.client import Client
		from fastmcp.client.transports import StreamableHttpTransport
	except Exception:
		return None

	transport = StreamableHttpTransport(url=mcp_url)
	mcp_client = getattr(app.state, "mcp_client", None) or Client(transport)
	async with mcp_client:
		# Try to get tool definitions from MCP client; fall back to empty
		tools = []
		try:
			get_defs = getattr(mcp_client, "getToolDefinitions", None)
			if callable(get_defs):
				tools = get_defs()
		except Exception:
			tools = []

		ai = AsyncOpenAI(api_key=openai_key)
		try:
			completion = await ai.chat.completions.create(
				model=model,
				messages=[
					{"role": "system", "content": prompts.SYSTEM_PROMPT},
					{"role": "user", "content": user_query},
				],
				tools=tools,
			)
			# Try to parse model output as JSON
			content = (completion.choices[0].message.content or "").strip()
			try:
				return json.loads(content)
			except Exception:
				return {"raw": content}
		except Exception:
			return None


