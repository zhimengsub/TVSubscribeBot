from pathlib import Path

ROOT = Path(__file__).parents[1]

CACHE_DB = ROOT / 'data' / 'cache.db'

CACHE_JSON = ROOT / 'data' / 'config.json'

PERSISTENCE = ROOT / 'data' / 'bot_pkl'

PERSISTENCE_UPDATE_INTERVAL = 60.0

