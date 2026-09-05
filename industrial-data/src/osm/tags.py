from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_free_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[\-_/]+", " ", text)
    text = re.sub(r"[^a-z0-9\s\.]+", " ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or None


def normalize_industrial_type(tags: dict[str, Any]) -> str | None:
    if not tags:
        return None

    if tags.get("landuse") == "industrial":
        return "landuse=industrial"
    if tags.get("building") == "industrial":
        return "building=industrial"
    if tags.get("industrial"):
        return f"industrial={tags['industrial']}"
    if tags.get("craft"):
        return f"craft={tags['craft']}"
    if tags.get("man_made"):
        return f"man_made={tags['man_made']}"
    if tags.get("amenity"):
        return f"amenity={tags['amenity']}"
    return None


def normalize_raw_tags(tags: dict[str, Any] | None) -> str:
    return json.dumps(tags or {}, sort_keys=True, ensure_ascii=False)


def industrial_tags_from_config(config: dict[str, Any]) -> dict[str, list[str]]:
    tags = config["osm"]["tags"]
    return {key: list(values) for key, values in tags.items()}


def tag_filters_from_config(config: dict[str, Any]) -> list[tuple[str, str | None]]:
    tag_groups = industrial_tags_from_config(config)
    filters: list[tuple[str, str | None]] = []
    for key, values in tag_groups.items():
        for value in values:
            filters.append((key, None if value == "*" else value))
    return filters
