"""
Universal Game MCP (UGM) - HOI4 Prebuilt Knowledge Retrieval Manager.
Provides on-demand strategic knowledge without flooding agent context:
- rules_and_modes (FAIR house bans, META guidelines)
- operating_loop (daily cadence, stopping predicates, standing plan)
- strategy_core (civilian vs military payoff, stockpile cover, force architecture)
- countries: germany_fair, china_fair, poland_meta
"""

from __future__ import annotations
import os
import json
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger("ugm.adapter.hoi4.knowledge")


class HOI4KnowledgeManager:
    """Loads and queries prebuilt Hearts of Iron IV domain knowledge on demand."""

    def __init__(self, knowledge_dir: Optional[str] = None):
        if not knowledge_dir:
            # Default to game_skills/hoi4/knowledge relative to project root
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            knowledge_dir = os.path.join(base_dir, "game_skills", "hoi4", "knowledge")
        self.knowledge_dir = os.path.abspath(knowledge_dir)

    def list_topics(self) -> List[str]:
        """Returns all available knowledge topics."""
        topics = ["rules_and_modes", "operating_loop", "strategy_core", "germany", "china", "poland"]
        return topics

    def get_knowledge(
        self,
        topic: str,
        subtopic: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retrieves compact, targeted knowledge for a specific topic and optional subtopic.
        Keeps agent context clean by loading only requested domain facets.
        """
        topic_lower = topic.lower().strip()
        file_map = {
            "rules": "rules_and_modes.json",
            "rules_and_modes": "rules_and_modes.json",
            "operating_loop": "operating_loop.json",
            "loop": "operating_loop.json",
            "strategy": "strategy_core.json",
            "strategy_core": "strategy_core.json",
            "germany": os.path.join("countries", "germany_fair.json"),
            "germany_fair": os.path.join("countries", "germany_fair.json"),
            "china": os.path.join("countries", "china_fair.json"),
            "china_fair": os.path.join("countries", "china_fair.json"),
            "poland": os.path.join("countries", "poland_meta.json"),
            "poland_meta": os.path.join("countries", "poland_meta.json"),
            "meta": "meta.json"
        }

        if topic_lower in ("country", "countries") and subtopic:
            rel_path = file_map.get(subtopic.lower().strip())
        else:
            rel_path = file_map.get(topic_lower)
        if not rel_path:
            return {
                "success": False,
                "error": f"Topic '{topic}' not found. Available topics: {self.list_topics()}",
                "available_topics": self.list_topics()
            }

        target_file = os.path.join(self.knowledge_dir, rel_path)
        if not os.path.isfile(target_file):
            return {
                "success": False,
                "error": f"Knowledge file missing at: {target_file}",
                "topic": topic
            }

        try:
            with open(target_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if subtopic and isinstance(data, dict):
                sub_lower = subtopic.lower()
                for k, v in data.items():
                    if k.lower() == sub_lower or sub_lower in k.lower():
                        return {
                            "success": True,
                            "topic": topic,
                            "subtopic": k,
                            "data": v
                        }
                return {
                    "success": True,
                    "topic": topic,
                    "subtopic_not_found": subtopic,
                    "available_sections": list(data.keys()),
                    "data": data
                }

            return {
                "success": True,
                "topic": topic,
                "data": data
            }
        except Exception as e:
            logger.error("Failed to read knowledge file %s: %s", target_file, e)
            return {
                "success": False,
                "error": str(e),
                "topic": topic
            }
