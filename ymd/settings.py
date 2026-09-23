import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from ymd import core

DEFAULT_CONFIG_DIR = Path.home() / ".ymd-gui"
SETTINGS_FILE = "settings.json"


@dataclass
class Profile:
    name: str = "default"
    token: str = ""
    output_dir: str = "."
    quality: int = 0
    lyrics_format: str = "none"
    embed_cover: bool = False
    skip_existing: bool = True
    path_pattern: str = str(core.DEFAULT_PATH_PATTERN)
    cover_resolution: int = core.DEFAULT_COVER_RESOLUTION
    delay: int = 0
    stick_to_artist: bool = False
    only_music: bool = False
    compatibility_level: int = 1
    timeout: int = 20
    tries: int = 20
    retry_delay: int = 5


class SettingsManager:
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or DEFAULT_CONFIG_DIR
        self._settings_path = self.config_dir / SETTINGS_FILE
        self._data: dict = {"active_profile": "default", "profiles": {}}
        self._load()

    def _load(self):
        if self._settings_path.exists():
            try:
                with open(self._settings_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        if "profiles" not in self._data:
            self._data["profiles"] = {}
        if "active_profile" not in self._data:
            self._data["active_profile"] = "default"
        # Ensure default profile exists
        if "default" not in self._data["profiles"]:
            self._data["profiles"]["default"] = asdict(Profile())

    def _save(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with open(self._settings_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def list_profiles(self) -> list[str]:
        return list(self._data["profiles"].keys())

    def get_profile(self, name: str) -> Profile:
        raw = self._data["profiles"].get(name)
        if raw is None:
            return Profile(name=name)
        return Profile(**raw)

    def save_profile(self, profile: Profile):
        self._data["profiles"][profile.name] = asdict(profile)
        self._save()

    def delete_profile(self, name: str):
        if name == "default":
            return  # don't delete default
        self._data["profiles"].pop(name, None)
        if self._data["active_profile"] == name:
            self._data["active_profile"] = "default"
        self._save()

    def get_active_profile_name(self) -> str:
        return self._data.get("active_profile", "default")

    def set_active_profile(self, name: str):
        self._data["active_profile"] = name
        self._save()

    def get_active_profile(self) -> Profile:
        name = self.get_active_profile_name()
        return self.get_profile(name)
