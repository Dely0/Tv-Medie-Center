"""播放器管理（浏览器内播放）"""
import logging
from typing import Optional

logger = logging.getLogger("player")

_playing = False
_current_title = ""
_current_episode = ""
_current_url = ""


def play(url: str, title: str = "", episode_title: str = "", referer: str = ""):
    global _playing, _current_title, _current_episode, _current_url
    stop()
    _current_title = title
    _current_episode = episode_title
    _current_url = url
    _playing = True
    logger.info(f"播放: {title} | {url[:60]}...")


def stop():
    global _playing
    _playing = False


def is_playing() -> bool:
    return _playing


def current_info() -> dict:
    return {
        "playing": is_playing(),
        "title": _current_title,
        "episode": _current_episode,
        "url": _current_url,
    }
