"""统一源注册表：MacCMS（主配置+社区）+ drpyS（JS 爬虫生态）。

上层（搜索/播放链/爬取/健康检查）只依赖本模块，不再区分源类型。
"""
import logging

logger = logging.getLogger("registry")


def get_search_sources() -> list:
    """返回可搜索/可播放的源（健康过滤 + 成人过滤）。"""
    from app.maccms_source import get_maccms_crawlable_sources
    from app.adult import is_enabled
    from app.ops.health import is_source_dead
    from app.ops.health import sorted_by_priority
    from app.source_framework.drpy_source import get_registry

    sources = list(get_maccms_crawlable_sources())
    adult_on = is_enabled()
    for s in get_registry().get_all():
        if not s.enabled or is_source_dead(s.name):
            continue
        if s.adult and not adult_on:
            continue
        sources.append(s)
    return sorted_by_priority(sources)


def get_drpy_enabled_sources(include_adult: bool = False) -> list:
    """仅返回 drpy 启用源（可选包含成人源）。"""
    from app.adult import is_enabled
    from app.ops.health import is_source_dead
    from app.ops.health import sorted_by_priority
    from app.source_framework.drpy_source import get_registry

    adult_on = is_enabled()
    out = []
    for s in get_registry().get_all():
        if not s.enabled or is_source_dead(s.name):
            continue
        if s.adult and (not adult_on or not include_adult):
            continue
        out.append(s)
    return sorted_by_priority(out)


def get_source_by_name(name: str):
    """按源名查找（MacCMS 优先，其次 drpy）。"""
    from app.maccms_source import get_manager
    src = get_manager().get_by_name(name)
    if src:
        return src
    from app.source_framework.drpy_source import get_registry
    return get_registry().get_by_name(name)


def get_source_count() -> dict:
    """各类型源数量（诊断用）。"""
    from app.maccms_source import get_manager
    from app.source_framework.drpy_source import get_registry
    return {
        "maccms": len(get_manager().get_all()),
        "drpy_total": len(get_registry().get_all()),
        "drpy_enabled": len(get_registry().get_enabled()),
    }
