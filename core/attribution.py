"""Attribution parser and normalizer for LLM FinOps."""

from pathlib import Path
from typing import Dict, Any, Optional
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "attribution.yaml"


class AttributionParser:
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.config = self._load_config()
        self.defaults = self.config.get("defaults", {
            "team": "unattributed",
            "feature": "unassigned",
            "user_id": "unknown_user",
            "env": "production"
        })
        self.header_mappings = self.config.get("header_mappings", {})
        self.feature_aliases = self.config.get("feature_aliases", {})

    def _load_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def normalize_string(self, value: Optional[Any], default_value: str) -> str:
        if value is None:
            return default_value
        s = str(value).strip()
        if not s or s.lower() in {"none", "null", "nan", "undefined", "unknown", ""}:
            return default_value
        s = s.lower().replace(" ", "-").replace("_", "-")
        # Remove repeated hyphens
        while "--" in s:
            s = s.replace("--", "-")
        return s.strip("-")

    def normalize_feature(self, feature: Optional[Any]) -> str:
        clean = self.normalize_string(feature, self.defaults.get("feature", "unassigned"))
        return self.feature_aliases.get(clean, clean)

    def normalize_team(self, team: Optional[Any]) -> str:
        return self.normalize_string(team, self.defaults.get("team", "unattributed"))

    def normalize_user(self, user: Optional[Any]) -> str:
        if user is None:
            return self.defaults.get("user_id", "unknown_user")
        s = str(user).strip()
        if not s or s.lower() in {"none", "null", "nan", "undefined"}:
            return self.defaults.get("user_id", "unknown_user")
        return s

    def parse_attribution(self, raw_data: Dict[str, Any]) -> Dict[str, str]:
        # Support case-insensitive key access for headers or dict keys
        lower_map = {str(k).lower(): v for k, v in raw_data.items()}

        team_val = (
            lower_map.get("x-team")
            or lower_map.get("team")
            or raw_data.get("team")
        )
        feature_val = (
            lower_map.get("x-feature")
            or lower_map.get("feature")
            or raw_data.get("feature")
        )
        user_val = (
            lower_map.get("x-user")
            or lower_map.get("x-user-id")
            or lower_map.get("user_id")
            or lower_map.get("user")
            or raw_data.get("user")
        )
        env_val = (
            lower_map.get("x-env")
            or lower_map.get("x-environment")
            or lower_map.get("env")
            or lower_map.get("environment")
            or self.defaults.get("env", "production")
        )

        team = self.normalize_team(team_val)
        feature = self.normalize_feature(feature_val)
        user_id = self.normalize_user(user_val)
        env = self.normalize_string(env_val, "production")

        is_unattributed = (
            team == self.defaults.get("team", "unattributed")
            or feature == self.defaults.get("feature", "unassigned")
            or team == "unattributed"
        )

        return {
            "team": team,
            "feature": feature,
            "user_id": user_id,
            "env": env,
            "is_unattributed": is_unattributed
        }
