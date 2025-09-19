import os
import gzip
import io
from typing import Optional

import httpx
from fastmcp import FastMCP


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
		resp = await _http_get(client, "/subtitles", params=query_params)
		data = resp.json()
		items = data.get("data", [])
		if not items:
			return f"No subtitles found for '{movie_name}' in '{language}'."

		# Pick the first subtitle entry
		sub = items[0]
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
			return "Subtitle file id not found in API response."

		# Request a temporary download link (requires Api-Key and Authorization)
		await _ensure_auth_token(client)
		dl_resp = await _http_post(client, "/download", json={"file_id": file_id}, include_auth=True)
		dl_info = dl_resp.json()
		dl_link = dl_info.get("link")
		if not dl_link:
			return "Failed to obtain download link for subtitles."

		file_resp = await client.get(dl_link, timeout=60)
		file_resp.raise_for_status()
		# httpx automatically decompresses gzip/deflate; handle plain .srt as-is
		payload = file_resp.content

		# Try decode as utf-8 with fallback
		try:
			return payload.decode("utf-8")
		except UnicodeDecodeError:
			try:
				return payload.decode("iso-8859-1")
			except Exception:
				return payload.decode(errors="ignore")


@mcp.tool()
async def verify_movie(name: str) -> bool:
	"""
	Given a name, verify whether it is a movie using OpenSubtitles features API.
	Returns True if at least one matching feature has type == 'movie'.
	"""
	params = {
		"query": name,
		"type": "movie",
		"page": 1,
		"per_page": 1,
	}
	async with httpx.AsyncClient(follow_redirects=True) as client:
		resp = await _http_get(client, "/features", params=params)
		print(resp.text)
		data = resp.json()
		items = data.get("data", [])
		if not items:
			return False
		for item in items:
			attributes = item.get("attributes", {})
			feature_type = (attributes.get("feature_type") or item.get("type") or "").lower()
			if feature_type == "movie":
				return True
		return False


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


