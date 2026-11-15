"""Space Heritage Explorer - backend API for "Abandoned but Not Forgotten".

Read-only JSON API over a small, validated NASA equipment dataset.
Run:  python app.py        (see README.md for configuration)
"""
import logging
import os
import random
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, request
from werkzeug.exceptions import BadRequest, HTTPException

from data_store import load_dataset, load_quiz

BASE_DIR = Path(__file__).resolve().parent
MAX_LIMIT = 100
log = logging.getLogger("space_heritage")


def _search_text(item):
    parts = [item[k] for k in ("name", "planet", "mission", "object_type", "location",
                               "status_label", "status_note", "description", "story")]
    return " ".join(parts).lower()


def create_app(equipment_file=None, quiz_file=None):
    app = Flask(__name__)
    app.json.ensure_ascii = False      # send real characters (–, ') instead of \uXXXX
    app.json.sort_keys = False         # keep field order from the data file
    app.url_map.strict_slashes = False  # /api/equipment and /api/equipment/ both work

    # Data is validated once at start-up; a bad data file stops the server immediately.
    store = load_dataset(equipment_file or BASE_DIR / "equipment.json")
    quiz_questions = load_quiz(quiz_file or BASE_DIR / "quiz.json", store["by_id"])
    equipment = store["equipment"]
    by_id = store["by_id"]
    meta = store["meta"]
    search_index = {item["id"]: _search_text(item) for item in equipment}

    allowed_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]

    # ---------------------------------------------------------------- helpers
    def int_arg(name, default, low, high):
        raw = request.args.get(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            raise BadRequest(f"'{name}' must be a whole number") from None
        if not low <= value <= high:
            raise BadRequest(f"'{name}' must be between {low} and {high}")
        return value

    def date_arg(name):
        raw = request.args.get(name, "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            raise BadRequest(f"'{name}' must be a date in YYYY-MM-DD format") from None

    def bool_arg(name):
        return request.args.get(name, "").strip().lower() in {"1", "true", "yes"}

    def text_arg(name):
        return request.args.get(name, "").strip().lower()

    # ------------------------------------------------------ cross-cutting hooks
    @app.after_request
    def add_headers(response):
        origin = request.headers.get("Origin")
        if "*" in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = "*"
        elif origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.method == "GET" and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=300"
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        message = "Route not found" if error.code == 404 else (error.description or error.name)
        response = jsonify({"error": message, "status": error.code})
        response.status_code = error.code
        if error.code == 405 and getattr(error, "valid_methods", None):
            response.headers["Allow"] = ", ".join(error.valid_methods)
        return response

    @app.errorhandler(Exception)
    def unexpected_error(error):
        if app.debug:
            raise error  # show the full debugger page only when debug is explicitly on
        log.exception("Unhandled error")
        return jsonify({"error": "Internal server error", "status": 500}), 500

    # ------------------------------------------------------------------ routes
    @app.get("/")
    def home():
        return jsonify({
            "project": "Abandoned but Not Forgotten",
            "message": "Space Heritage Explorer API is running",
            "endpoints": [
                "/api/health", "/api/meta", "/api/statuses", "/api/planets",
                "/api/equipment", "/api/equipment/<id>", "/api/map",
                "/api/timeline", "/api/quiz",
            ],
        })

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "records": len(equipment)})

    @app.get("/api/meta")
    def get_meta():
        by_planet, by_status = {}, {}
        for item in equipment:
            by_planet[item["planet"]] = by_planet.get(item["planet"], 0) + 1
            by_status[item["status"]] = by_status.get(item["status"], 0) + 1
        return jsonify({
            "dataset": meta["dataset"], "version": meta["version"],
            "last_reviewed": meta["last_reviewed"], "conventions": meta["conventions"],
            "disclaimer": meta["disclaimer"],
            "counts": {"total": len(equipment), "by_planet": by_planet, "by_status": by_status},
        })

    @app.get("/api/statuses")
    def statuses():
        return jsonify(meta["status_definitions"])

    @app.get("/api/planets")
    def planets():
        return jsonify(sorted({item["planet"] for item in equipment}))

    @app.get("/api/equipment")
    def get_equipment():
        # Filters (all optional, case-insensitive):
        #   planet=Mars   mission=Pathfinder (partial match)   type=rover (rover/lander/or the full label)
        #   status=mission_ended   q=water dust (every word must match)   limit=, offset=
        planet = text_arg("planet")
        mission = text_arg("mission")
        kind = text_arg("type")
        status = text_arg("status")
        tokens = text_arg("q").split()
        limit = int_arg("limit", MAX_LIMIT, 1, MAX_LIMIT)
        offset = int_arg("offset", 0, 0, 10_000)

        matched = []
        for item in equipment:
            if planet and item["planet"].lower() != planet:
                continue
            if mission and mission not in item["mission"].lower():
                continue
            if kind and kind not in (item["category"], item["object_type"].lower()):
                continue
            if status and item["status"] != status:
                continue
            if tokens and not all(t in search_index[item["id"]] for t in tokens):
                continue
            matched.append(item)

        page = matched[offset:offset + limit]
        return jsonify({
            "count": len(matched),
            "returned": len(page),
            "limit": limit,
            "offset": offset,
            "filters": {"planet": planet or None, "mission": mission or None, "type": kind or None,
                        "status": status or None, "q": " ".join(tokens) or None},
            "results": page,
        })

    @app.get("/api/equipment/<equipment_id>")
    def equipment_detail(equipment_id):
        item = by_id.get(equipment_id)
        if item is None:
            return jsonify({"error": "Equipment not found", "id": equipment_id, "status": 404}), 404
        return jsonify(item)

    @app.get("/api/map")
    def map_markers():
        """Lightweight pin list for the 3D Moon/Mars explorer."""
        planet = text_arg("planet")
        markers = [{
            "id": i["id"], "name": i["name"], "planet": i["planet"], "category": i["category"],
            "status": i["status"], "lat": i["coordinates"]["lat"], "lon_east": i["coordinates"]["lon_east"],
            "coordinate_kind": i["coordinates"]["kind"],
        } for i in equipment if not planet or i["planet"].lower() == planet]
        return jsonify({"count": len(markers), "results": markers})

    @app.get("/api/timeline")
    def timeline():
        """All dated events across the dataset, oldest first. Optional: planet, from, to (YYYY-MM-DD)."""
        planet = text_arg("planet")
        start, end = date_arg("from"), date_arg("to")
        if start and end and start > end:
            raise BadRequest("'from' must not be later than 'to'")
        events = []
        for item in equipment:
            if planet and item["planet"].lower() != planet:
                continue
            for e in item["timeline"]:
                if (start and e["date"] < start) or (end and e["date"] > end):
                    continue
                events.append({"date": e["date"], "kind": e["kind"], "event": e["event"],
                               "equipment_id": item["id"], "equipment_name": item["name"],
                               "planet": item["planet"], "mission": item["mission"]})
        events.sort(key=lambda e: (e["date"], e["equipment_id"]))
        return jsonify({"count": len(events), "results": events})

    @app.get("/api/quiz")
    def get_quiz():
        """Quiz questions written from the verified records. Optional: planet, n (1-50), shuffle=true."""
        planet = text_arg("planet")
        n = int_arg("n", 5, 1, 50)
        pool = [q for q in quiz_questions if not planet or q["planet"].lower() == planet]
        if bool_arg("shuffle"):
            pool = random.sample(pool, len(pool))
        chosen = pool[:n]
        return jsonify({"count": len(chosen), "results": chosen})

    return app


app = create_app()

if __name__ == "__main__":
    # Safe defaults: local only, no debugger. Opt in explicitly:
    #   FLASK_DEBUG=1 python app.py          (development only)
    #   HOST=0.0.0.0 PORT=8000 python app.py (to expose on the network)
    logging.basicConfig(level=logging.INFO)
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
