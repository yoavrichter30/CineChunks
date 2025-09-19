import os
import json
import logging
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional, Any, Dict
import prompts as prompts


try:
	from dotenv import load_dotenv
	load_dotenv()
except Exception:
	pass

app = FastAPI(title="CineChunks Web")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Basic logging setup
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("cinechunks-web")


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
			try:
				mcp_tools = await client.list_tools()
				# Convert MCP tools to OpenAI format
				app.state.mcp_tools = []
				for tool in mcp_tools:
					if hasattr(tool, 'name') and hasattr(tool, 'description'):
						openai_tool = {
							"type": "function",
							"function": {
								"name": tool.name,
								"description": tool.description,
								"parameters": getattr(tool, 'inputSchema', {})
							}
						}
						app.state.mcp_tools.append(openai_tool)
			except Exception as e:
				logger.warning(f"Could not fetch MCP tools during startup: {e}")
				app.state.mcp_tools = []
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
    logger.info(f"/submit received: movie_name='{movie_name}', episodes='{episodes}', episode_length='{episode_length}'")
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
    logger.info(f"Built user_query='{user_query}'")
    result = await ask_chatgpt_via_mcp(user_query)
    logger.info(f"ask_chatgpt_via_mcp returned type={type(result).__name__}")
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
		logger.error("OPENAI_API_KEY is not set; cannot call ChatGPT")
		return None

	try:
		from openai import AsyncOpenAI
		from fastmcp.client import Client
		from fastmcp.client.transports import StreamableHttpTransport
	except Exception as e:
		logger.exception(f"Failed to import OpenAI/FastMCP clients: {e}")
		return None

	# Use cached tools from startup only
	tools = getattr(app.state, "mcp_tools", [])
	logger.info(f"Using {len(tools)} cached MCP tool definitions")

	ai = AsyncOpenAI(api_key=openai_key)
	try:
		logger.info("Calling OpenAI chat.completions.create ...")
		completion = await ai.chat.completions.create(
            max_tokens=10000,
			model=model,
			messages=[
				{"role": "system", "content": prompts.SYSTEM_PROMPT},
				{"role": "user", "content": user_query},
			],
			tools=tools,
		)
		# Handle the response - could be tool calls or final JSON
		message = completion.choices[0].message
		content = (message.content or "").strip()
		tool_calls = getattr(message, 'tool_calls', None)
		
		logger.info(f"OpenAI response length={len(content)} chars")
		logger.info(f"Tool calls: {len(tool_calls) if tool_calls else 0}")
		
		# If there are tool calls, execute them and get the final response
		if tool_calls:
			logger.info("Model is calling tools, executing them...")
			
			# Add the assistant's message with tool calls to the conversation
			messages = [
				{"role": "system", "content": prompts.SYSTEM_PROMPT},
				{"role": "user", "content": user_query},
				{"role": "assistant", "content": content, "tool_calls": tool_calls}
			]
			
			# Execute each tool call
			for tool_call in tool_calls:
				tool_name = tool_call.function.name
				tool_args = json.loads(tool_call.function.arguments)
				tool_call_id = tool_call.id
				
				logger.info(f"Executing tool: {tool_name} with args: {tool_args}")
				
				try:
					# Get the MCP client and execute the tool
					transport = StreamableHttpTransport(url=mcp_url)
					mcp_client = getattr(app.state, "mcp_client", None) or Client(transport)
					async with mcp_client:
						result = await mcp_client.call_tool(tool_name, arguments=tool_args)
					
					# Add tool result to messages
					messages.append({
						"role": "tool",
						"tool_call_id": tool_call_id,
						"content": str(result)
					})
					
					logger.info(f"Tool {tool_name} result: {str(result)[:100]}...")
					
				except Exception as e:
					logger.error(f"Error executing tool {tool_name}: {e}")
					messages.append({
						"role": "tool",
						"tool_call_id": tool_call_id,
						"content": f"Error: {str(e)}"
					})
			
			# Get the final response from the model with tool results
			logger.info("Getting final response from model...")
			final_completion = await ai.chat.completions.create(
				model=model,
				response_format={"type": "json_object"},
				messages=messages,
			)
			
			final_content = (final_completion.choices[0].message.content or "").strip()
			logger.info(f"Final response length={len(final_content)} chars")
			
			try:
				parsed_json = json.loads(final_content)
				logger.info("Successfully parsed final JSON response")
				return parsed_json
			except Exception as e:
				logger.warning(f"Final response was not valid JSON: {e}")
				logger.info(f"Raw final content: {repr(final_content)}")
				return {"raw": final_content}
		
		# If no tool calls, try to parse as JSON directly
		if not content:
			logger.warning("Model returned empty content")
			return {"error": "Model returned empty response"}
		
		try:
			print(content)
			parsed_json = json.loads(content)
			logger.info("Successfully parsed JSON response")
			return parsed_json
		except Exception as e:
			logger.warning(f"Response was not valid JSON: {e}")
			logger.info(f"Raw content: {repr(content)}")
			return {"raw": content}
	except Exception as e:
		error_msg = str(e).lower()
		if "rate limit" in error_msg or "quota" in error_msg or "billing" in error_msg:
			logger.error(f"OpenAI free tier limitation: {e}")
			return {"error": "OpenAI free tier rate limit reached. Please try again later or upgrade your plan."}
		elif "authentication" in error_msg or "invalid" in error_msg:
			logger.error(f"OpenAI API key issue: {e}")
			return {"error": "Invalid OpenAI API key. Please check your OPENAI_API_KEY."}
		else:
			logger.exception(f"OpenAI chat completion failed: {e}")
			return {"error": f"OpenAI API error: {str(e)}"}


