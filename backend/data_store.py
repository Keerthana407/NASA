"""Loads and validates the Space Heritage dataset.

The API refuses to start if the data is malformed, so a typo in
equipment.json is caught at start-up instead of crashing a request later.
"""
import json
import re
from datetime import date
from pathlib import Path

PLANETS = {"Moon", "Mars"}
CATEGORIES = {"rover", "lander", "descent_stage", "orbiter", "instrument", "other"}
EVENT_KINDS = {"landing", "deployment", "departure", "last_contact", "mission_end"}
COORD_KINDS = {"landing_site", "rover_position"}
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

REQUIRED_TEXT = (
    "id", "name", "planet", "mission", "object_type", "category", "location",
    "status", "status_label", "status_note", "description", "story",
)


class DataError(Exception):
    """Raised when a data file does not meet the schema."""


def _require(condition, message):
    if not condition:
        raise DataError(message)


def _text(record, key, where):
    value = record.get(key)
    _require(isinstance(value, str) and value.strip(), f"{where}: '{key}' must be a non-empty string")
    return value


def _iso_date(value, where):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise DataError(f"{where}: '{value}' is not a valid YYYY-MM-DD date") from None


def _validate_record(record, status_definitions):
    _require(isinstance(record, dict), "every equipment entry must be an object")
    where = f"equipment '{record.get('id', '?')}'"
    for key in REQUIRED_TEXT:
        _text(record, key, where)

    _require(ID_PATTERN.match(record["id"]), f"{where}: id must be a lowercase slug")
    _require(record["planet"] in PLANETS, f"{where}: planet must be one of {sorted(PLANETS)}")
    _require(record["category"] in CATEGORIES, f"{where}: category must be one of {sorted(CATEGORIES)}")
    _require(record["status"] in status_definitions, f"{where}: unknown status '{record['status']}'")
    _require(isinstance(record.get("year"), int), f"{where}: 'year' must be an integer")
    _require(isinstance(record.get("operational"), bool), f"{where}: 'operational' must be true or false")
    _require(
        record["operational"] == (record["status"] == "operational"),
        f"{where}: 'operational' must be true only when status is 'operational'",
    )

    coords = record.get("coordinates")
    _require(isinstance(coords, dict), f"{where}: 'coordinates' is required")
    _require(isinstance(coords.get("lat"), (int, float)) and -90 <= coords["lat"] <= 90, f"{where}: lat must be between -90 and 90")
    _require(isinstance(coords.get("lon_east"), (int, float)) and -180 <= coords["lon_east"] <= 180, f"{where}: lon_east must be between -180 and 180")
    _require(coords.get("kind") in COORD_KINDS, f"{where}: coordinates.kind must be one of {sorted(COORD_KINDS)}")
    for key in ("precision", "source_name", "source_url"):
        _text(coords, key, f"{where} coordinates")

    timeline = record.get("timeline")
    _require(isinstance(timeline, list) and timeline, f"{where}: 'timeline' must be a non-empty list")
    for event in timeline:
        _require(isinstance(event, dict), f"{where}: timeline entries must be objects")
        _iso_date(event.get("date"), f"{where} timeline")
        _require(event.get("kind") in EVENT_KINDS, f"{where}: timeline kind must be one of {sorted(EVENT_KINDS)}")
        _text(event, "event", f"{where} timeline")
    record["timeline"] = sorted(timeline, key=lambda e: e["date"])
    kinds = [e["kind"] for e in record["timeline"]]
    _require(kinds.count("landing") == 1, f"{where}: timeline needs exactly one 'landing' event")
    landing_year = int(next(e for e in record["timeline"] if e["kind"] == "landing")["date"][:4])
    _require(record["year"] == landing_year, f"{where}: 'year' ({record['year']}) must match the landing year ({landing_year})")

    if record["status"] == "left_on_surface":
        _require("departure" in kinds, f"{where}: 'left_on_surface' needs a 'departure' event")
    if record["status"] == "mission_ended":
        _require("mission_end" in kinds or "last_contact" in kinds,
                 f"{where}: 'mission_ended' needs a 'last_contact' or 'mission_end' event")

    figures = record.get("key_figures", {})
    _require(isinstance(figures, dict) and all(isinstance(v, (int, float)) for v in figures.values()),
             f"{where}: 'key_figures' must map names to numbers")
    record["key_figures"] = figures

    sources = record.get("sources")
    _require(isinstance(sources, list) and sources, f"{where}: needs at least one source")
    for source in sources:
        _text(source, "name", f"{where} source")
        _require(str(source.get("url", "")).startswith("https://"), f"{where}: source urls must start with https://")
        _require(isinstance(source.get("checked"), bool), f"{where}: source 'checked' must be true or false")
    # Keep the original two fields so existing frontends keep working.
    record["source_name"] = sources[0]["name"]
    record["source_url"] = sources[0]["url"]
    return record


def load_dataset(path):
    """Return {'meta', 'equipment', 'by_id'} after validating the file."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataError(f"cannot read {path}: {exc}") from exc

    _require(isinstance(raw, dict) and "meta" in raw and "equipment" in raw,
             "equipment file must be an object with 'meta' and 'equipment'")
    meta = raw["meta"]
    definitions = meta.get("status_definitions")
    _require(isinstance(definitions, dict) and definitions, "meta.status_definitions is required")

    equipment = [_validate_record(r, definitions) for r in raw["equipment"]]
    ids = [r["id"] for r in equipment]
    _require(len(ids) == len(set(ids)), "equipment ids must be unique")
    return {"meta": meta, "equipment": equipment, "by_id": {r["id"]: r for r in equipment}}


def load_quiz(path, equipment_by_id):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataError(f"cannot read {path}: {exc}") from exc

    questions = raw.get("questions") if isinstance(raw, dict) else None
    _require(isinstance(questions, list), "quiz file must contain a 'questions' list")
    seen = set()
    for q in questions:
        where = f"quiz question '{q.get('id', '?')}'"
        for key in ("id", "equipment_id", "question", "correct_option_id", "explanation"):
            _text(q, key, where)
        _require(q["id"] not in seen, f"{where}: duplicate id")
        seen.add(q["id"])
        _require(q["equipment_id"] in equipment_by_id, f"{where}: unknown equipment_id")
        options = q.get("options")
        _require(isinstance(options, list) and len(options) >= 2, f"{where}: needs at least two options")
        option_ids = [o.get("id") for o in options]
        _require(len(option_ids) == len(set(option_ids)), f"{where}: option ids must be unique")
        _require(q["correct_option_id"] in option_ids, f"{where}: correct_option_id is not one of the options")
        q["planet"] = equipment_by_id[q["equipment_id"]]["planet"]
    return questions
