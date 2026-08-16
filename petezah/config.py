import os
import sys

from dotenv import load_dotenv

load_dotenv()

PREFIX = "p!"
DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot_data.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.log")

SUPERUSER_ID = int(os.getenv("SUPERUSER_ID", "1311722282317779097"))

DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()

GROQ_MODELS = []
_env_model = (os.getenv("GROQ_MODEL") or "").strip()
if _env_model:
    GROQ_MODELS.append(_env_model)
for _m in ("llama-3.1-8b-instant", "openai/gpt-oss-20b"):
    if _m not in GROQ_MODELS:
        GROQ_MODELS.append(_m)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TIMEOUT = 20
GROQ_MAX_TOKENS = 512
GROQ_TEMPERATURE = 0.6
AI_HISTORY = 8
AI_USER_LIMIT = 4
AI_USER_WINDOW = 60
AI_CHANNEL_LIMIT = 10
AI_CHANNEL_WINDOW = 60
AI_BUSY_TIMEOUT = 25
IMAGE_USER_LIMIT = 2
IMAGE_USER_WINDOW = 180
INVITE_TIMEOUT_SECONDS = 60
PURGE_MAX = 100
SLOWMODE_MAX = 21600
TIMEOUT_MAX_SECONDS = 28 * 24 * 3600
ACTION_PACE = 0.55
SETUP_PACE = 0.65
LOCK_PACE = 0.6
ANTINUKE_PACE = 0.5

if not DISCORD_TOKEN:
    sys.stderr.write("Missing DISCORD_TOKEN in environment.\n")
    sys.exit(1)
if not GROQ_API_KEY:
    sys.stderr.write("Missing GROQ_API_KEY in environment.\n")
    sys.exit(1)
