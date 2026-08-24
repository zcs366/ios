#!/usr/bin/env python3
"""skill_discovery.py — IO-S↔ISN桥接syscall

openLLM四体集成的关键一环：让IO-S的planner/audit/agent
能在运行时查询ISN有哪些可用skill，并调用匹配的skill执行。

API:
  skill_discovery.search(query, limit=10) → [skill_summary]
  skill_discovery.list(category=None) → [skill_summary]
  skill_discovery.get(name) → skill_detail
  skill_discovery.dispatch(name, goal, context) → result
  skill_discovery.stats() → 系统统计

三层信息分级（遵循ISN协议）:
  索引层: SA/planner可见 — 名称·描述·分类·版本·健康
  定义层: 仅ZA可见 — 完整prompt（不进SA上下文）
  元数据层: ISN可见 — 版本历史·质量信号·优化记录

IO-S通过索引层发现skill，通过ZA执行skill。
SA（调用者）从头到尾看不到skill的定义层prompt。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("io-s.skill-discovery")

# === 路径 ===
HERMES_SKILLS_DIR = Path.home() / ".hermes" / "skills"
ISN_DIR = Path.home() / "isn"
ISN_CASE_LIB = ISN_DIR / "case_library.json"


@dataclass
class SkillSummary:
    """索引层 — SA可见的skill元数据。"""
    name: str
    description: str
    category: str
    tags: list[str]
    version: str
    healthy: bool
    path: str                       # 相对于HERMES_SKILLS_DIR的路径

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description[:120] + ("..." if len(self.description) > 120 else ""),
            "category": self.category,
            "tags": [t for t in self.tags[:5] if t.strip()],
            "version": self.version,
            "healthy": self.healthy,
        }


@dataclass
class SkillDetail:
    """定义层 — 含完整prompt（不进SA上下文）。"""
    summary: SkillSummary
    full_description: str
    prompt_length: int
    files: list[str]
    last_modified: float

    def to_dict(self) -> dict:
        d = self.summary.to_dict()
        d["full_description"] = self.full_description
        d["prompt_length"] = self.prompt_length
        d["files"] = self.files
        d["last_modified"] = self.last_modified
        return d


class SkillCatalog:
    """ISN skill目录 — 从Hermes skills目录实时构建。"""

    def __init__(self):
        self._cache: dict[str, SkillSummary] = {}
        self._cache_timestamp: float = 0
        self._cache_ttl: float = 60.0  # 60秒缓存

    def _parse_skill_dir(self, skill_dir: Path, category: str,
                         skills: dict[str, SkillSummary]):
        """解析单个skill目录，提取元数据加入skills字典。"""
        skill_name = skill_dir.name

        # 找SKILL.md或其它md文件
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.exists():
            md_files = list(skill_dir.glob("*.md"))
            if not md_files:
                return
            skill_path = md_files[0]

        # 解析frontmatter
        description = ""
        tags: list[str] = []
        version = "0.0"
        try:
            content = skill_path.read_text(encoding="utf-8", errors="replace")
            fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
            if fm_match:
                fm_text = fm_match.group(1)
                desc_match = re.search(r'description:\s*"(.*?)"', fm_text)
                if desc_match:
                    description = desc_match.group(1)
                else:
                    desc_match = re.search(r"description:\s*'(.*?)'", fm_text)
                    if desc_match:
                        description = desc_match.group(1)
                tag_match = re.search(r'tags:\s*\[(.*?)\]', fm_text)
                if tag_match:
                    raw = tag_match.group(1)
                    tags = [t.strip().strip("'\"") for t in raw.split(",") if t.strip()]
                ver_match = re.search(r'version:\s*([\d.]+)', fm_text)
                if ver_match:
                    version = ver_match.group(1)
            else:
                for line in content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("---"):
                        description = line[:200]
                        break
        except Exception:
            pass

        if not description:
            description = f"{skill_name}: (无描述)"

        skills[skill_name] = SkillSummary(
            name=skill_name,
            description=description,
            category=category,
            tags=tags,
            version=version,
            healthy=True,
            path=str(skill_path.relative_to(HERMES_SKILLS_DIR.parent) if skill_path else ""),
        )

    def _scan_skills(self) -> dict[str, SkillSummary]:
        """扫描Hermes skills目录，构建skill目录。"""
        now = time.time()
        if self._cache and (now - self._cache_timestamp) < self._cache_ttl:
            return self._cache

        skills: dict[str, SkillSummary] = {}

        if not HERMES_SKILLS_DIR.exists():
            logger.warning(f"❌ Hermes skills目录不存在: {HERMES_SKILLS_DIR}")
            return skills

        for entry in sorted(HERMES_SKILLS_DIR.iterdir()):
            if not entry.is_dir():
                continue

            # 检查是否本身就是一个skill（有SKILL.md在根目录）
            # 如 skills/jika/SKILL.md, skills/web-access/SKILL.md
            if (entry / "SKILL.md").exists():
                self._parse_skill_dir(entry, "uncategorized", skills)
                continue

            # 否则这是一个category目录，遍历其下的skill
            for skill_dir in sorted(entry.iterdir()):
                if not skill_dir.is_dir():
                    continue
                self._parse_skill_dir(skill_dir, entry.name, skills)

        self._cache = skills
        self._cache_timestamp = now
        logger.info(f"📋 ISN skill目录扫描: {len(skills)}个skill")
        return skills

    def search(self, query: str, limit: int = 10) -> list[SkillSummary]:
        """按名称/描述/标签搜索skill。"""
        skills = self._scan_skills()
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if w.strip()]

        if not query_words:
            return []

        scored: list[tuple[float, SkillSummary]] = []

        for skill in skills.values():
            score = 0.0

            # 名称精确匹配 → 高分
            if query_lower == skill.name.lower():
                score += 10.0
            elif query_lower in skill.name.lower():
                score += 5.0

            # 名称部分匹配（将连字符/下划线转为空格）
            name_lower = skill.name.lower().replace("-", " ").replace("_", " ")
            for word in query_words:
                if word in name_lower:
                    score += 3.0

            # 描述匹配
            desc_lower = skill.description.lower()
            for word in query_words:
                if word in desc_lower:
                    score += 1.5

            # 标签匹配（跳过空标签和单字符标签的瞎匹配）
            for tag in skill.tags:
                tag_lower = tag.lower().strip()
                if not tag_lower or len(tag_lower) <= 2:
                    continue
                for word in query_words:
                    if word in tag_lower or tag_lower in word:
                        score += 2.0

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:limit]]

    def list_by_category(self, category: str | None = None) -> list[SkillSummary]:
        """按分类列出skill。"""
        skills = self._scan_skills()
        if category:
            return [s for s in skills.values() if s.category == category]
        return list(skills.values())

    def get(self, name: str) -> SkillSummary | None:
        """按名称获取单个skill。"""
        skills = self._scan_skills()
        return skills.get(name)

    def get_detail(self, name: str) -> SkillDetail | None:
        """获取skill详情（含完整prompt — 不进SA上下文）。"""
        summary = self.get(name)
        if not summary:
            return None

        # 找skill文件
        # 可能在category子目录下，也可能在顶层
        candidates = [
            HERMES_SKILLS_DIR / summary.category / name / "SKILL.md",
            HERMES_SKILLS_DIR / name / "SKILL.md",
        ]
        skill_path = None
        for p in candidates:
            if p.exists():
                skill_path = p
                break

        if not skill_path:
            return None

        try:
            content = skill_path.read_text(encoding="utf-8", errors="replace")
            files = [f.name for f in skill_path.parent.iterdir() if f.is_file()]
            mtime = skill_path.stat().st_mtime

            fm_match = re.match(r'^---\s*\n.*?\n---\s*\n(.*)', content, re.DOTALL)
            full_desc = fm_match.group(1).strip()[:500] if fm_match else content[:500]

            return SkillDetail(
                summary=summary,
                full_description=full_desc,
                prompt_length=len(content),
                files=files,
                last_modified=mtime,
            )
        except Exception as e:
            logger.warning(f"❌ 读取skill详情失败: {name}: {e}")
            return None

    def stats(self) -> dict:
        """系统统计。"""
        skills = self._scan_skills()
        categories: dict[str, int] = {}
        for s in skills.values():
            categories[s.category] = categories.get(s.category, 0) + 1

        total = len(skills)
        healthy = sum(1 for s in skills.values() if s.healthy)

        return {
            "total_skills": total,
            "healthy": healthy,
            "unhealthy": total - healthy,
            "categories": dict(sorted(categories.items(), key=lambda x: -x[1])),
            "cache_age_seconds": round(time.time() - self._cache_timestamp, 1) if self._cache_timestamp else 0,
        }


def register(kernel):
    catalog = SkillCatalog()

    def handle_search(pid, query="", limit=10):
        results = catalog.search(query, limit=limit)
        return {"query": query, "count": len(results), "results": [s.to_dict() for s in results]}

    def handle_list(pid, category=None):
        results = catalog.list_by_category(category=category)
        return {"category": category or "all", "count": len(results), "results": [s.to_dict() for s in results]}

    def handle_get(pid, name=""):
        detail = catalog.get_detail(name)
        if detail:
            return detail.to_dict()
        return {"error": f"skill '{name}' not found"}

    def handle_dispatch(pid, name="", goal="", context=""):
        summary = catalog.get(name)
        if not summary:
            return {"success": False, "error": f"skill '{name}' not found"}
        detail = catalog.get_detail(name)
        return {
            "success": True,
            "skill": name,
            "category": summary.category,
            "goal": goal,
            "prompt_size": detail.prompt_length if detail else 0,
            "note": "ZA执行待集成 — 当前返回skill信息，执行需delegate_task",
        }

    def handle_stats(pid):
        return catalog.stats()

    kernel.register("skill_discovery.search", handle_search)
    kernel.register("skill_discovery.list", handle_list)
    kernel.register("skill_discovery.get", handle_get)
    kernel.register("skill_discovery.dispatch", handle_dispatch)
    kernel.register("skill_discovery.stats", handle_stats)
    kernel._skill_catalog = catalog
    logger.info("✅ IO-S↔ISN桥接已注册: skill_discovery (search/list/get/dispatch/stats)")
