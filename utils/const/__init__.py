"""一些常量"""
from modules.utils.const._others import *
from modules.utils.const._path import *
from modules.utils.const._signal import *

NOT_SET = object()

USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/90.0.4430.72 Safari/537.36"
)
REQUEST_HEADERS: dict = {"User-Agent": USER_AGENT}
