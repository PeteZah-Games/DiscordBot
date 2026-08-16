import asyncio
import json
import os
from copy import deepcopy
from typing import Any

from petezah.config import DATA_FILE

DEFAULT_GUILD: dict[str, Any] = {
    "configured": False,
    "member_role_ids": [],
    "staff_role_ids": [],
    "mute_role_id": None,
    "quarantine_role_id": None,
    "log_channel_id": None,
    "welcome_channel_id": None,
    "welcome_message": "",
    "ai_channels": [],
    "locked": {},
    "invite_filter_server": False,
    "invite_filter_channels": [],
    "nuke_protection": False,
    "whitelist": [],
    "pins": {},
    "temp_actions": [],
}


def _default_guild() -> dict[str, Any]:
    return deepcopy(DEFAULT_GUILD)


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    if "guilds" in raw:
        data = raw
        data.setdefault("guilds", {})
        data.setdefault("warnings", {})
        data.setdefault("autowarn", {})
        data.setdefault("reaction_roles", [])
        data.setdefault("afk", {})
        for gid, g in list(data["guilds"].items()):
            merged = _default_guild()
            merged.update(g)
            data["guilds"][str(gid)] = merged
        return data
    data = {
        "guilds": {},
        "warnings": {},
        "autowarn": {},
        "reaction_roles": [],
        "afk": raw.get("afk", {}),
    }
    for cid in raw.get("active_channels", []):
        data.setdefault("_orphan_ai", []).append(int(cid))
    for gid, ch in (raw.get("log_channels") or {}).items():
        g = data["guilds"].setdefault(str(gid), _default_guild())
        g["log_channel_id"] = int(ch)
        g["configured"] = True
    for gid in raw.get("nuke_protection_servers", []):
        g = data["guilds"].setdefault(str(gid), _default_guild())
        g["nuke_protection"] = True
    for gid in raw.get("security_servers", []):
        g = data["guilds"].setdefault(str(gid), _default_guild())
        g["invite_filter_server"] = True
    for cid in raw.get("security_channels", []):
        data.setdefault("_orphan_security", []).append(int(cid))
    for cid, msg in (raw.get("welcome_channels") or {}).items():
        data.setdefault("_orphan_welcome", []).append((int(cid), msg))
    for cid in raw.get("locked_channels", []):
        data.setdefault("_orphan_locked", []).append(int(cid))
    autowarn = raw.get("autowarn_rules") or {}
    for gid, rules in autowarn.items():
        data["autowarn"][str(gid)] = rules
    return data


class Store:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {
            "guilds": {},
            "warnings": {},
            "autowarn": {},
            "reaction_roles": [],
            "afk": {},
        }
        self._lock = asyncio.Lock()
        self.orphan_ai: list[int] = []
        self.orphan_security: list[int] = []
        self.orphan_welcome: list[tuple[int, str]] = []
        self.orphan_locked: list[int] = []

    async def load(self) -> None:
        if not os.path.isfile(DATA_FILE):
            return
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return
        migrated = _migrate(raw)
        self.orphan_ai = list(migrated.pop("_orphan_ai", []))
        self.orphan_security = list(migrated.pop("_orphan_security", []))
        self.orphan_welcome = list(migrated.pop("_orphan_welcome", []))
        self.orphan_locked = list(migrated.pop("_orphan_locked", []))
        self._data = migrated

    async def save(self) -> None:
        async with self._lock:
            payload = json.dumps(self._data, indent=2, default=str)
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, DATA_FILE)

    def guild(self, guild_id: int) -> dict[str, Any]:
        key = str(guild_id)
        if key not in self._data["guilds"]:
            self._data["guilds"][key] = _default_guild()
        return self._data["guilds"][key]

    def warnings(self, guild_id: int) -> dict[str, list]:
        key = str(guild_id)
        self._data["warnings"].setdefault(key, {})
        return self._data["warnings"][key]

    def autowarn(self, guild_id: int) -> dict[str, str]:
        key = str(guild_id)
        self._data["autowarn"].setdefault(key, {})
        return self._data["autowarn"][key]

    @property
    def reaction_roles(self) -> list[dict[str, Any]]:
        return self._data["reaction_roles"]

    @property
    def afk(self) -> dict[str, str]:
        return self._data["afk"]
