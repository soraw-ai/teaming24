"""Agent, skill, and tool persistence mixin."""

from __future__ import annotations

import json
import time
from typing import Any


class AgentSkillMixin:
    """CRUD for agents, skills, assignments, and custom tools."""

    def save_agent(self, agent_data: dict[str, Any]):
        """Create or update an agent."""
        agent_id = agent_data.get("id")
        if not agent_id:
            agent_id = f"agent_{int(time.time() * 1000)}"

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO agents
                (id, name, type, status, capabilities, endpoint, model, goal, backstory,
                 tools, system_prompt, allow_delegation, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        COALESCE((SELECT created_at FROM agents WHERE id = ?), ?), ?)
                """,
                (
                    agent_id,
                    agent_data.get("name", ""),
                    agent_data.get("type", "worker"),
                    agent_data.get("status", "offline"),
                    json.dumps(agent_data.get("capabilities", [])),
                    agent_data.get("endpoint"),
                    agent_data.get("model"),
                    agent_data.get("goal"),
                    agent_data.get("backstory"),
                    json.dumps(agent_data.get("tools", [])),
                    agent_data.get("system_prompt", ""),
                    1 if agent_data.get("allow_delegation", True) else 0,
                    json.dumps(agent_data.get("metadata", {})),
                    agent_id,
                    time.time(),
                    time.time(),
                ),
            )
        return agent_id

    def get_agents(self, agent_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """List persisted agents."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if agent_type:
                cursor.execute(
                    "SELECT * FROM agents WHERE type = ? ORDER BY created_at DESC LIMIT ?",
                    (agent_type, limit),
                )
            else:
                cursor.execute(
                    "SELECT * FROM agents ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            result = []
            for row in cursor.fetchall():
                entry = dict(row)
                entry["capabilities"] = type(self)._safe_json_loads(entry.get("capabilities"), [])
                entry["tools"] = type(self)._safe_json_loads(entry.get("tools"), [])
                entry["metadata"] = type(self)._safe_json_loads(entry.get("metadata"), {})
                if "allow_delegation" in entry:
                    entry["allow_delegation"] = bool(entry["allow_delegation"])
                result.append(entry)
            return result

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Get a single agent by ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
            row = cursor.fetchone()
            if row:
                entry = dict(row)
                entry["capabilities"] = type(self)._safe_json_loads(entry.get("capabilities"), [])
                entry["tools"] = type(self)._safe_json_loads(entry.get("tools"), [])
                entry["metadata"] = type(self)._safe_json_loads(entry.get("metadata"), {})
                if "allow_delegation" in entry:
                    entry["allow_delegation"] = bool(entry["allow_delegation"])
                return entry
            return None

    def update_agent(self, agent_id: str, updates: dict[str, Any]):
        """Partially update an agent."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            fields = []
            values = []
            for key in ["name", "type", "status", "endpoint", "model", "goal", "backstory", "system_prompt"]:
                if key in updates:
                    fields.append(f"{key} = ?")
                    values.append(updates[key])
            if "allow_delegation" in updates:
                fields.append("allow_delegation = ?")
                values.append(1 if updates["allow_delegation"] else 0)
            for json_field in ["capabilities", "tools", "metadata"]:
                if json_field in updates:
                    fields.append(f"{json_field} = ?")
                    values.append(json.dumps(updates[json_field]))
            fields.append("updated_at = ?")
            values.append(time.time())
            values.append(agent_id)

            if fields:
                cursor.execute(
                    f"UPDATE agents SET {', '.join(fields)} WHERE id = ?",
                    values,
                )

    def delete_agent(self, agent_id: str):
        """Delete an agent."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM agents WHERE id = ?", (agent_id,))

    def save_skill(self, skill_data: dict[str, Any]):
        """Create or update a skill."""
        now = time.time()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO skills
                    (id, name, description, instructions, category, tags,
                     author, version, license, compatibility,
                     requires, enabled, source, file_path,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        COALESCE((SELECT created_at FROM skills WHERE id = ?), ?),
                        ?)
                """,
                (
                    skill_data.get("id"),
                    skill_data.get("name"),
                    skill_data.get("description", ""),
                    skill_data.get("instructions", ""),
                    skill_data.get("category", "general"),
                    json.dumps(skill_data.get("tags", [])),
                    skill_data.get("author", ""),
                    skill_data.get("version", "1.0.0"),
                    skill_data.get("license", ""),
                    skill_data.get("compatibility", ""),
                    json.dumps(skill_data.get("requires", {})),
                    1 if skill_data.get("enabled", True) else 0,
                    skill_data.get("source", "user"),
                    skill_data.get("file_path", ""),
                    skill_data.get("id"),
                    skill_data.get("created_at", now),
                    now,
                ),
            )

    def get_skills(self) -> list[dict[str, Any]]:
        """Get all skills."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM skills ORDER BY name")
            result = []
            for row in cursor.fetchall():
                entry = dict(row)
                entry["tags"] = type(self)._safe_json_loads(entry.get("tags"), [])
                entry["requires"] = type(self)._safe_json_loads(entry.get("requires"), {})
                entry["enabled"] = bool(entry.get("enabled", 1))
                result.append(entry)
            return result

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        """Get a single skill by ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM skills WHERE id = ?", (skill_id,))
            row = cursor.fetchone()
            if row:
                entry = dict(row)
                entry["tags"] = type(self)._safe_json_loads(entry.get("tags"), [])
                entry["requires"] = type(self)._safe_json_loads(entry.get("requires"), {})
                entry["enabled"] = bool(entry.get("enabled", 1))
                return entry
            return None

    def update_skill(self, skill_id: str, updates: dict[str, Any]):
        """Partially update a skill."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            fields = []
            values = []
            for key in [
                "name",
                "description",
                "instructions",
                "category",
                "author",
                "version",
                "license",
                "compatibility",
                "source",
                "file_path",
            ]:
                if key in updates:
                    fields.append(f"{key} = ?")
                    values.append(updates[key])
            if "enabled" in updates:
                fields.append("enabled = ?")
                values.append(1 if updates["enabled"] else 0)
            for json_field in ["tags", "requires"]:
                if json_field in updates:
                    fields.append(f"{json_field} = ?")
                    values.append(json.dumps(updates[json_field]))
            fields.append("updated_at = ?")
            values.append(time.time())
            values.append(skill_id)
            if fields:
                cursor.execute(
                    f"UPDATE skills SET {', '.join(fields)} WHERE id = ?",
                    values,
                )

    def delete_skill(self, skill_id: str):
        """Delete a skill and its agent assignments."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM agent_skills WHERE skill_id = ?", (skill_id,))
            cursor.execute("DELETE FROM skills WHERE id = ?", (skill_id,))

    def assign_skills_to_agent(self, agent_id: str, skill_ids: list[str]):
        """Replace all skill assignments for an agent."""
        now = time.time()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM agent_skills WHERE agent_id = ?", (agent_id,))
            for skill_id in skill_ids:
                cursor.execute(
                    "INSERT OR IGNORE INTO agent_skills (agent_id, skill_id, assigned_at) VALUES (?, ?, ?)",
                    (agent_id, skill_id, now),
                )

    def get_agent_skills(self, agent_id: str) -> list[dict[str, Any]]:
        """Get all skills assigned to an agent."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT s.* FROM skills s
                JOIN agent_skills ags ON s.id = ags.skill_id
                WHERE ags.agent_id = ?
                ORDER BY s.name
                """,
                (agent_id,),
            )
            result = []
            for row in cursor.fetchall():
                entry = dict(row)
                entry["tags"] = type(self)._safe_json_loads(entry.get("tags"), [])
                entry["requires"] = type(self)._safe_json_loads(entry.get("requires"), {})
                entry["enabled"] = bool(entry.get("enabled", 1))
                result.append(entry)
            return result

    def get_agent_skill_ids(self, agent_id: str) -> list[str]:
        """Get skill IDs assigned to an agent."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT skill_id FROM agent_skills WHERE agent_id = ?", (agent_id,))
            return [row["skill_id"] for row in cursor.fetchall()]

    def save_custom_tool(self, name: str, description: str, category: str = "custom"):
        """Create or update a custom tool definition."""
        now = time.time()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO custom_tools (name, description, category, enabled, created_at, updated_at)
                VALUES (?, ?, ?, 1, COALESCE((SELECT created_at FROM custom_tools WHERE name = ?), ?), ?)
                """,
                (name, description, category, name, now, now),
            )

    def get_custom_tools(self) -> list[dict[str, Any]]:
        """Get all custom tools."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM custom_tools ORDER BY name")
            return [dict(row) for row in cursor.fetchall()]

    def delete_custom_tool(self, name: str):
        """Delete a custom tool."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM custom_tools WHERE name = ?", (name,))
