from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select

from . import db
from .models import SkillResponse, SkillRow


def _rowdict(r: SkillRow) -> dict:
    return {
        "id": r.id, "title": r.title, "category": r.category, "kind": r.kind,
        "enabled": r.enabled, "priority": r.priority, "icon": r.icon,
        "match": r.match, "responses": r.responses, "commands": r.commands,
        "examples": r.examples, "config": r.config,
    }


_FIELDS = ("title", "category", "kind", "enabled", "priority", "icon",
           "match", "responses", "commands", "examples", "config")


class PgSkillStore:
    async def ensure_seeded(self, defs: list[dict]) -> None:
        async with db.sessionmaker()() as s, s.begin():
            existing = set((await s.scalars(select(SkillRow.id))).all())
            for d in defs:
                if d["id"] in existing:
                    continue
                s.add(SkillRow(
                    id=d["id"], title=d.get("title", ""), category=d.get("category", "other"),
                    kind=d.get("kind", "say"), enabled=bool(d.get("enabled", True)),
                    priority=int(d.get("priority", 0)), icon=d.get("icon", ""),
                    match=d.get("match", []), responses=d.get("responses", []),
                    commands=d.get("commands", []), examples=d.get("examples", []),
                    config=d.get("config", {})))

    async def upsert(self, d: dict) -> dict:
        async with db.sessionmaker()() as s, s.begin():
            r = await s.get(SkillRow, d["id"])
            if r is None:
                r = SkillRow(id=d["id"])
                s.add(r)
            for k in _FIELDS:
                if k in d:
                    setattr(r, k, d[k])
            r.updated_at = datetime.now(timezone.utc)
            await s.flush()
            return _rowdict(r)

    async def load_defs(self) -> list[dict]:
        async with db.sessionmaker()() as s:
            rows = (await s.scalars(select(SkillRow))).all()
            return [_rowdict(r) for r in rows]

    async def update(self, skill_id: str, fields: dict) -> dict | None:
        async with db.sessionmaker()() as s, s.begin():
            r = await s.get(SkillRow, skill_id)
            if r is None:
                return None
            for k, v in fields.items():
                if k != "id" and hasattr(r, k):
                    setattr(r, k, v)
            r.updated_at = datetime.now(timezone.utc)
            return _rowdict(r)

    async def random_response(self, skill_id: str, fallback: list[str]) -> str | None:
        async with db.sessionmaker()() as s:
            txt = await s.scalar(
                select(SkillResponse.text).where(SkillResponse.skill_id == skill_id)
                .order_by(func.random()).limit(1))
        if txt:
            return txt
        return random.choice(fallback) if fallback else None

    async def add_responses(self, skill_id: str, texts: list[str]) -> int:
        async with db.sessionmaker()() as s, s.begin():
            if await s.get(SkillRow, skill_id) is None:
                return 0
            for t in texts:
                if t:
                    s.add(SkillResponse(skill_id=skill_id, text=t))
            return len([t for t in texts if t])

    async def count_responses(self, skill_id: str) -> int:
        async with db.sessionmaker()() as s:
            return int(await s.scalar(
                select(func.count()).select_from(SkillResponse)
                .where(SkillResponse.skill_id == skill_id)) or 0)


class MemorySkillStore:
    def __init__(self):
        self._defs: dict[str, dict] = {}
        self._pool: dict[str, list[str]] = defaultdict(list)

    async def ensure_seeded(self, defs: list[dict]) -> None:
        for d in defs:
            if d["id"] not in self._defs:
                self._defs[d["id"]] = dict(d)

    async def load_defs(self) -> list[dict]:
        return [dict(d) for d in self._defs.values()]

    async def update(self, skill_id: str, fields: dict) -> dict | None:
        d = self._defs.get(skill_id)
        if d is None:
            return None
        for k, v in fields.items():
            if k != "id":
                d[k] = v
        return dict(d)

    async def upsert(self, d: dict) -> dict:
        cur = self._defs.get(d["id"], {"id": d["id"]})
        for k in _FIELDS:
            if k in d:
                cur[k] = d[k]
        cur["id"] = d["id"]
        self._defs[d["id"]] = cur
        return dict(cur)

    async def random_response(self, skill_id: str, fallback: list[str]) -> str | None:
        pool = self._pool.get(skill_id) or fallback
        return random.choice(pool) if pool else None

    async def add_responses(self, skill_id: str, texts: list[str]) -> int:
        added = [t for t in texts if t]
        self._pool[skill_id].extend(added)
        return len(added)

    async def count_responses(self, skill_id: str) -> int:
        return len(self._pool.get(skill_id, ()))
