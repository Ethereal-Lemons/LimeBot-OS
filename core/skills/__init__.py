"""
Skill System for LimeBot.

Provides modular, extensible skill management:
- SkillLoader: Discovers and parses skills
- SkillRegistry: Manages skills and injects docs into LLM
- SkillExecutor: Runs skill scripts
- SkillAPI: Backend API handlers for skills
"""

from .loader import SkillLoader
from .registry import SkillRegistry
from .executor import SkillExecutor
from .api import SkillAPI

__all__ = ["SkillLoader", "SkillRegistry", "SkillExecutor", "SkillAPI"]
