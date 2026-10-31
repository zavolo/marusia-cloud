from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("emu.skills")

_lock = threading.Lock()
_by_id: dict[str, "Skill"] = {}
_order: list[str] = []
_store = None


@dataclass
class Skill:
    id: str
    title: str = ""
    category: str = "other"
    kind: str = "say"
    enabled: bool = True
    priority: int = 0
    icon: str = ""
    match: list = field(default_factory=list)
    responses: list = field(default_factory=list)
    commands: list = field(default_factory=list)
    examples: list = field(default_factory=list)
    config: dict = field(default_factory=dict)


def _mk(item: dict) -> Skill:
    return Skill(
        id=item["id"],
        title=item.get("title", ""),
        category=item.get("category", "other"),
        kind=item.get("kind", "say"),
        enabled=bool(item.get("enabled", True)),
        priority=int(item.get("priority", 0)),
        icon=item.get("icon", ""),
        match=item.get("match", []),
        responses=item.get("responses", []),
        commands=item.get("commands", []),
        examples=item.get("examples", []),
        config=item.get("config", {}),
    )


def _seed_path() -> str:
    env = os.environ.get("MARUSIA_SKILLS_FILE")
    if env:
        return env
    return str(Path(__file__).resolve().parent.parent.parent / "data" / "skills.json")


CONTENT = {"humor": "humor.json", "skill_list": "skill_list.json"}


def _read_seed() -> list:
    try:
        return json.loads(Path(_seed_path()).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.warning("сид каталога скиллов не загружен (%s): %s", _seed_path(), e)
        return []


def _read_content(fname: str) -> list:
    base = Path(_seed_path()).resolve().parent / "content"
    try:
        data = json.loads((base / fname).read_text(encoding="utf-8"))
        return [str(x) for x in data if x]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


async def _seed_content() -> None:
    for sid, fname in CONTENT.items():
        if get(sid) is None:
            continue
        if await count_responses(sid) > 0:
            continue
        texts = _read_content(fname)
        if texts:
            await add_responses(sid, texts)
            log.info("контент скилла %s: залито %d ответов", sid, len(texts))


async def init(app) -> None:
    global _store
    from ..storage import db
    from ..storage.skills import MemorySkillStore, PgSkillStore
    _store = PgSkillStore() if db.enabled() else MemorySkillStore()
    await _store.ensure_seeded(_read_seed())
    await reload()
    await _seed_content()
    log.info("каталог скиллов в %s: %d скиллов",
             "Postgres" if db.enabled() else "in-memory", len(_by_id))


async def reload() -> None:
    defs = await _store.load_defs()
    with _lock:
        _by_id.clear()
        _by_id.update({d["id"]: _mk(d) for d in defs})
        _order[:] = sorted(_by_id, key=lambda i: (-_by_id[i].priority, i))


def active() -> list[Skill]:
    with _lock:
        return [_by_id[i] for i in _order if _by_id[i].enabled]


def all_skills() -> list[Skill]:
    with _lock:
        return [_by_id[i] for i in _order]


def get(skill_id: str) -> Skill | None:
    return _by_id.get(skill_id)


async def set_override(skill_id: str, fields: dict) -> Skill | None:
    if _store is None:
        return None
    row = await _store.update(skill_id, fields)
    if row is None:
        return None
    await reload()
    return _by_id.get(skill_id)


async def upsert(skill_def: dict) -> Skill | None:
    if _store is None:
        return None
    await _store.upsert(skill_def)
    await reload()
    return _by_id.get(skill_def["id"])


async def random_response(skill_id: str) -> str | None:
    sk = _by_id.get(skill_id)
    fallback = sk.responses if sk else []
    if _store is None:
        import random
        return random.choice(fallback) if fallback else None
    return await _store.random_response(skill_id, fallback)


async def add_responses(skill_id: str, texts: list[str]) -> int:
    if _store is None:
        return 0
    return await _store.add_responses(skill_id, texts)


async def count_responses(skill_id: str) -> int:
    if _store is None:
        return 0
    return await _store.count_responses(skill_id)


def normalize(s: str) -> str:
    s = (s or "").lower().replace("ё", "е")
    return " ".join(s.split())


def _tok(t: str) -> str:
    return t.lower().replace("ё", "е")


def matches(sk: Skill, norm: str) -> bool:
    for rule in sk.match:
        toks = rule.get("all")
        if toks and all(_tok(t) in norm for t in toks):
            return True
        toks = rule.get("any")
        if toks and any(_tok(t) in norm for t in toks):
            return True
    return False


def as_dict(sk: Skill) -> dict:
    return {
        "id": sk.id, "title": sk.title, "category": sk.category, "kind": sk.kind,
        "enabled": sk.enabled, "priority": sk.priority, "icon": sk.icon,
        "examples": sk.examples, "match": sk.match, "config": sk.config,
    }
