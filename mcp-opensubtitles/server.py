import os
import gzip
import io
import logging
from typing import Optional

import httpx
from fastmcp import FastMCP

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


OPEN_SUBTITLES_BASE_URL = "https://api.opensubtitles.com/api/v1"


AUTH_TOKEN: Optional[str] = os.getenv("OPEN_SUBTITLES_TOKEN", "").strip() or None


def _build_headers(include_auth: bool = False) -> dict:
	api_key = os.getenv("OPEN_SUBTITLES_API_KEY", "").strip()
	user_agent = os.getenv("OPEN_SUBTITLES_USER_AGENT", "CineChunksMCP/1.0").strip()
	if not api_key:
		raise RuntimeError("OPEN_SUBTITLES_API_KEY is required. Set it in environment or .env")
	headers = {
		"Api-Key": api_key,
		"User-Agent": user_agent,
		"Accept": "application/json",
		"Accept-Encoding": "gzip",
	}
	if include_auth and AUTH_TOKEN:
		headers["Authorization"] = f"Bearer {AUTH_TOKEN}"
	return headers


def _gunzip_if_needed(content: bytes, headers: httpx.Headers) -> bytes:
	encoding = headers.get("Content-Encoding", "").lower()
	if "gzip" in encoding and content:
		with gzip.GzipFile(fileobj=io.BytesIO(content)) as gz:
			return gz.read()
	return content


mcp = FastMCP("OpenSubtitles MCP Server")


async def _http_get(client: httpx.AsyncClient, path: str, params: Optional[dict] = None) -> httpx.Response:
	url = f"{OPEN_SUBTITLES_BASE_URL}{path}"
	resp = await client.get(url, params=params, headers=_build_headers(), timeout=30)
	resp.raise_for_status()
	return resp


async def _http_post(client: httpx.AsyncClient, path: str, json: Optional[dict] = None, *, include_auth: bool = False) -> httpx.Response:
	url = f"{OPEN_SUBTITLES_BASE_URL}{path}"
	resp = await client.post(url, json=json, headers=_build_headers(include_auth=include_auth), timeout=30)
	resp.raise_for_status()
	return resp


async def _ensure_auth_token(client: httpx.AsyncClient) -> None:
	global AUTH_TOKEN
	if AUTH_TOKEN:
		return
	username = os.getenv("OPEN_SUBTITLES_USERNAME", "").strip()
	password = os.getenv("OPEN_SUBTITLES_PASSWORD", "").strip()
	if not (username and password):
		return
	# Try to login to obtain a bearer token
	resp = await client.post(
		f"{OPEN_SUBTITLES_BASE_URL}/login",
		headers=_build_headers(),
		json={"username": username, "password": password},
		timeout=30,
	)
	resp.raise_for_status()
	data = resp.json() if resp.content else {}
	AUTH_TOKEN = data.get("token") or data.get("access_token") or AUTH_TOKEN


@mcp.tool()
async def download_subtitles(movie_name: str, language: str = "en") -> str:
	"""
	Given a movie name, search for subtitles and return subtitle text (SRT if available).

	- movie_name: Title to search.
	- language: ISO 639-1 code, default "en".
	"""
	logger.info(f"Starting subtitle download for movie: '{movie_name}' in language: '{language}'")
	
	query_params = {
		"query": movie_name,
		"languages": language,
		"type": "movie",
		"order_by": "download_count",
		"order_direction": "desc",
		"ai_translated": "exclude",
		"hearing_impaired": "exclude",
	}
	
	async with httpx.AsyncClient(follow_redirects=True) as client:
		logger.info(f"Searching for subtitles with params: {query_params}")
		resp = await _http_get(client, "/subtitles", params=query_params)
		data = resp.json()
		items = data.get("data", [])
		
		logger.info(f"Found {len(items)} subtitle results")
		if not items:
			logger.warning(f"No subtitles found for '{movie_name}' in '{language}'")
			return f"No subtitles found for '{movie_name}' in '{language}'."

		# Pick the first subtitle entry
		sub = items[0]
		logger.info(f"Selected subtitle: {sub.get('attributes', {}).get('title', 'Unknown title')}")
		
		file_id = None
		# New API often provides a list of files under attributes.files
		attributes = sub.get("attributes", {})
		files = attributes.get("files") or []
		if files:
			file_id = files[0].get("file_id")
		if not file_id:
			# fallback to id
			file_id = sub.get("id")
		if not file_id:
			logger.error("Subtitle file id not found in API response")
			return "Subtitle file id not found in API response."

		logger.info(f"Requesting download link for file_id: {file_id}")
		# Request a temporary download link (requires Api-Key and Authorization)
		await _ensure_auth_token(client)
		dl_resp = await _http_post(client, "/download", json={"file_id": file_id}, include_auth=True)
		dl_info = dl_resp.json()
		dl_link = dl_info.get("link")
		if not dl_link:
			logger.error("Failed to obtain download link for subtitles")
			return "Failed to obtain download link for subtitles."

		logger.info(f"Downloading subtitle file from: {dl_link}")
		file_resp = await client.get(dl_link, timeout=60)
		file_resp.raise_for_status()
		# httpx automatically decompresses gzip/deflate; handle plain .srt as-is
		payload = file_resp.content
		
		logger.info(f"Downloaded {len(payload)} bytes of subtitle data")

		# Try decode as utf-8 with fallback
		try:
			decoded_content = payload.decode("utf-8")
			logger.info("Successfully decoded subtitle content as UTF-8")
			return decoded_content
		except UnicodeDecodeError:
			try:
				decoded_content = payload.decode("iso-8859-1")
				logger.info("Successfully decoded subtitle content as ISO-8859-1")
				return decoded_content
			except Exception as e:
				logger.warning(f"Failed to decode subtitle content, using error='ignore': {e}")
				return payload.decode(errors="ignore")




if __name__ == "__main__":
	try:
		from dotenv import load_dotenv
		load_dotenv()
	except Exception:
		pass

	host = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
	port_str = os.getenv("MCP_HTTP_PORT", "8000")
	try:
		port = int(port_str)
	except ValueError:
		port = 8000
	path = os.getenv("MCP_HTTP_PATH", "/mcp")

	mcp.run(transport="streamable-http", host=host, port=port, path=path)


