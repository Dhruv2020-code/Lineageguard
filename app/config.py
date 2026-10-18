import os
from dotenv import load_dotenv

load_dotenv()

FALKOR_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKOR_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKOR_USERNAME = os.getenv("FALKORDB_USERNAME") or None
FALKOR_PASSWORD = os.getenv("FALKORDB_PASSWORD") or None
GRAPH_NAME = os.getenv("GRAPH_NAME", "lineageguard")
MEMORY_GRAPH_NAME = os.getenv("MEMORY_GRAPH_NAME", "lineageguard_memory")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")