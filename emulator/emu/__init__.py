"""Marusia / VK Capsula Neo — эмулятор облака (phrase-протокол + управление)."""
from .app import make_app
from .config import Config

__all__ = ["make_app", "Config"]
