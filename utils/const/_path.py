"""目录常量"""
import os
from pathlib import Path

__all__ = [
    "PROJECT_ROOT",
    "PACKAGE_ROOT",
    "CORE_DIR",
    "SERVICE_DIR",
    "PLUGIN_DIR",
    "RESOURCE_DIR",
    "CACHE_DIR",
]

# 项目根目录
PROJECT_ROOT = Path(os.curdir).absolute()
# 包的根目录
PACKAGE_ROOT = Path(__file__).joinpath("../../../").resolve()
# Core 目录
CORE_DIR = PACKAGE_ROOT / "core"
# Services 目录
SERVICE_DIR = PACKAGE_ROOT / "services"
# 插件目录
PLUGIN_DIR = PACKAGE_ROOT / "plugins"
# 资源目录
RESOURCE_DIR = PACKAGE_ROOT / "resources"
# cache 目录
CACHE_DIR = PACKAGE_ROOT / "cache"

if not CACHE_DIR.exists():
    CACHE_DIR.mkdir(exist_ok=True, parents=True)
