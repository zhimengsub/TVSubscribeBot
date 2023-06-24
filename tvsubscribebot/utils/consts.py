from pathlib import Path

ROOT = Path(__file__).parents[1]

CACHE_DB = ROOT / 'cache.db'

CACHE_JSON = ROOT / 'config.json'

PERSISTENCE = ROOT / 'bot_pkl'

PERSISTENCE_UPDATE_INTERVAL = 60.0

