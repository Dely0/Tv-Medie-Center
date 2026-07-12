"""Pydantic 数据模型"""
from pydantic import BaseModel
from typing import Optional


class VideoInfo(BaseModel):
    id: int
    title: str
    type: str  # movie / tv / variety / anime
    cover: Optional[str] = None
    description: Optional[str] = None
    year: Optional[int] = None
    area: Optional[str] = None
    director: Optional[str] = None
    actors: Optional[str] = None
    rating: Optional[float] = None
    source: Optional[str] = None


class EpisodeInfo(BaseModel):
    id: int
    video_id: int
    episode_num: int
    episode_title: Optional[str] = None
    play_url: Optional[str] = None
    is_available: bool = True


class VideoDetail(VideoInfo):
    episodes: list[EpisodeInfo] = []


class HistoryRecord(BaseModel):
    video_id: int
    episode_id: Optional[int] = None
    progress_seconds: float = 0
    total_seconds: float = 0


class SearchResult(BaseModel):
    results: list[VideoInfo]
    total: int
    page: int


class HomePage(BaseModel):
    categories: list[dict]
    sections: list[dict]  # [{name, type, videos: [VideoInfo]}]


class PlayResponse(BaseModel):
    play_url: str
    title: str
    episode_title: Optional[str] = None
