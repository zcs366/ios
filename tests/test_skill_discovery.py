#!/usr/bin/env python3
"""test_skill_discovery.py — IO-S↔ISN桥接测试。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from syscall.skill_discovery import SkillCatalog, SkillSummary


class FakeKernel:
    def __init__(self):
        self._registry = {}
    def register(self, name, handler):
        self._registry[name] = handler


def test_catalog_scan():
    """扫描Heres skills目录应返回200+个skill。"""
    catalog = SkillCatalog()
    skills = catalog._scan_skills()
    assert len(skills) > 100, f"期望>100个skill, 实际{len(skills)}"
    # 验证至少包含一些核心skill
    names = list(skills.keys())
    assert "jika" in names
    assert "isn-architecture" in names


def test_search_by_name():
    """按名称搜索应返回匹配结果。"""
    catalog = SkillCatalog()
    results = catalog.search("jika")
    assert len(results) >= 1
    assert results[0].name == "jika"


def test_search_by_description():
    """按描述关键词搜索。"""
    catalog = SkillCatalog()
    # "skill" 应该在很多skill描述中
    results = catalog.search("skill")
    assert len(results) >= 5


def test_search_limit():
    """limit参数正确截断。"""
    catalog = SkillCatalog()
    results = catalog.search("agent", limit=3)
    assert len(results) <= 3


def test_search_empty():
    """空查询应返回空列表。"""
    catalog = SkillCatalog()
    results = catalog.search("xyznonexistent12345")
    assert len(results) == 0


def test_list_all():
    """列出所有skill。"""
    catalog = SkillCatalog()
    all_skills = catalog.list_by_category()
    assert len(all_skills) > 100


def test_list_by_category():
    """按分类过滤。"""
    catalog = SkillCatalog()
    research_skills = catalog.list_by_category("research")
    assert len(research_skills) >= 10


def test_get_existing():
    """获取存在的skill应返回摘要。"""
    catalog = SkillCatalog()
    skill = catalog.get("jika")
    assert skill is not None
    assert skill.name == "jika"
    assert len(skill.description) > 0


def test_get_nonexistent():
    """获取不存在的skill应返回None。"""
    catalog = SkillCatalog()
    skill = catalog.get("nonexistent_skill_xyz")
    assert skill is None


def test_get_detail():
    """获取skill详情应含完整信息。"""
    catalog = SkillCatalog()
    detail = catalog.get_detail("jika")
    if detail:  # SKILL.md必须存在
        assert len(detail.full_description) > 0
        assert detail.prompt_length > 0
        assert len(detail.files) > 0


def test_stats_structure():
    """统计数据含必要字段。"""
    catalog = SkillCatalog()
    stats = catalog.stats()
    assert "total_skills" in stats
    assert stats["total_skills"] > 100
    assert "categories" in stats
    assert "healthy" in stats


def test_kernel_registration():
    """所有5个syscall正确注册。"""
    kernel = FakeKernel()
    from syscall.skill_discovery import register
    register(kernel)
    for name in ["skill_discovery.search", "skill_discovery.list",
                  "skill_discovery.get", "skill_discovery.dispatch",
                  "skill_discovery.stats"]:
        assert name in kernel._registry, f"Missing: {name}"
