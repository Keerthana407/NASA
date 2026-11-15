"""Run from the backend folder:  python -m unittest discover -s tests -v"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from data_store import DataError, load_dataset, load_quiz  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
RAW = json.loads((BACKEND / "equipment.json").read_text(encoding="utf-8"))


def write_temp(data):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(data, handle)
    handle.close()
    return handle.name


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()

    def get(self, url):
        response = self.client.get(url)
        return response, response.get_json()

    # ---- basics
    def test_home_and_health(self):
        self.assertEqual(self.get("/")[0].status_code, 200)
        _, body = self.get("/api/health")
        self.assertEqual(body, {"status": "ok", "records": 7})

    def test_planets_and_statuses(self):
        self.assertEqual(self.get("/api/planets")[1], ["Mars", "Moon"])
        statuses = self.get("/api/statuses")[1]
        self.assertIn("left_on_surface", statuses)
        self.assertIn("operational", statuses)

    def test_meta_counts(self):
        counts = self.get("/api/meta")[1]["counts"]
        self.assertEqual(counts["total"], 7)
        self.assertEqual(counts["by_planet"], {"Moon": 3, "Mars": 4})
        self.assertEqual(counts["by_status"], {"left_on_surface": 3, "mission_ended": 4})

    # ---- equipment filters
    def test_all_equipment_keeps_original_shape(self):
        _, body = self.get("/api/equipment")
        self.assertEqual(body["count"], 7)
        for key in ("count", "filters", "results"):
            self.assertIn(key, body)
        for key in ("id", "name", "planet", "mission", "object_type", "year", "location",
                    "status", "status_note", "description", "story", "source_name", "source_url"):
            self.assertIn(key, body["results"][0])

    def test_type_filter_works_for_moon_and_mars_rovers(self):
        # Regression: 'type=Rover' used to miss the Moon rovers ("Lunar rover").
        self.assertEqual(self.get("/api/equipment?type=Rover")[1]["count"], 6)
        self.assertEqual(self.get("/api/equipment?planet=Moon&type=Rover")[1]["count"], 3)
        self.assertEqual(self.get("/api/equipment?planet=Mars&type=rover")[1]["count"], 3)
        self.assertEqual(self.get("/api/equipment?type=Lunar%20rover")[1]["count"], 3)
        self.assertEqual(self.get("/api/equipment?type=lander")[1]["count"], 1)

    def test_status_filter_separates_abandoned_from_ended(self):
        left = self.get("/api/equipment?status=left_on_surface")[1]
        self.assertTrue(all(r["planet"] == "Moon" for r in left["results"]))
        ended = self.get("/api/equipment?status=mission_ended")[1]
        self.assertTrue(all(r["planet"] == "Mars" for r in ended["results"]))

    def test_search_requires_every_word(self):
        self.assertEqual([r["id"] for r in self.get("/api/equipment?q=water")[1]["results"]], ["spirit", "opportunity"])
        self.assertEqual(self.get("/api/equipment?q=hadley%20rille")[1]["count"], 1)
        self.assertEqual(self.get("/api/equipment?q=hadley%20gusev")[1]["count"], 0)
        self.assertEqual(self.get("/api/equipment?q=%20")[1]["count"], 7)

    def test_mission_filter_is_partial_and_case_insensitive(self):
        self.assertEqual(self.get("/api/equipment?mission=pathfinder")[1]["count"], 2)

    def test_pagination(self):
        body = self.get("/api/equipment?limit=2&offset=1")[1]
        self.assertEqual((body["count"], body["returned"], len(body["results"])), (7, 2, 2))
        self.assertEqual(self.get("/api/equipment?limit=0")[0].status_code, 400)
        self.assertEqual(self.get("/api/equipment?limit=abc")[0].status_code, 400)
        self.assertEqual(self.get("/api/equipment?offset=-1")[0].status_code, 400)

    def test_detail_and_404(self):
        response, body = self.get("/api/equipment/sojourner")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["key_figures"]["sols_operated"], 83)
        response, body = self.get("/api/equipment/nope")
        self.assertEqual((response.status_code, body["error"], body["id"]), (404, "Equipment not found", "nope"))
        self.assertEqual(self.get("/api/equipment/")[1]["count"], 7)  # trailing slash tolerated

    # ---- errors are always JSON
    def test_errors_are_json(self):
        response = self.client.post("/api/equipment")
        self.assertEqual(response.status_code, 405)
        self.assertTrue(response.is_json)
        self.assertIn("GET", response.headers["Allow"])
        response = self.client.get("/api/missing")
        self.assertEqual((response.status_code, response.get_json()["error"]), (404, "Route not found"))

    # ---- headers
    def test_headers(self):
        response = self.client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("max-age", response.headers["Cache-Control"])
        self.assertNotIn("Cache-Control", self.client.get("/api/equipment?limit=abc").headers)
        self.assertEqual(self.client.options("/api/equipment").status_code, 200)

    def test_cors_can_be_restricted(self):
        import os
        os.environ["CORS_ORIGINS"] = "http://localhost:5173"
        try:
            client = create_app().test_client()
        finally:
            del os.environ["CORS_ORIGINS"]
        ok = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
        bad = client.get("/api/health", headers={"Origin": "http://evil.example"})
        self.assertEqual(ok.headers["Access-Control-Allow-Origin"], "http://localhost:5173")
        self.assertNotIn("Access-Control-Allow-Origin", bad.headers)

    def test_non_ascii_is_not_escaped(self):
        self.assertIn("–", self.client.get("/api/equipment/apollo-15-lrv").get_data(as_text=True))

    # ---- map, timeline, quiz
    def test_map(self):
        _, body = self.get("/api/map")
        self.assertEqual(body["count"], 7)
        self.assertEqual(self.get("/api/map?planet=Moon")[1]["count"], 3)
        marker = body["results"][0]
        self.assertTrue(-90 <= marker["lat"] <= 90 and -180 <= marker["lon_east"] <= 180)

    def test_timeline(self):
        events = self.get("/api/timeline")[1]["results"]
        self.assertEqual([e["date"] for e in events], sorted(e["date"] for e in events))
        self.assertEqual(events[0]["date"], "1971-07-30")
        self.assertEqual(self.get("/api/timeline?planet=Moon")[1]["count"], 6)
        self.assertEqual(self.get("/api/timeline?from=2010-01-01&to=2011-12-31")[1]["count"], 2)
        self.assertEqual(self.get("/api/timeline?from=2020-01-01&to=2010-01-01")[0].status_code, 400)
        self.assertEqual(self.get("/api/timeline?from=yesterday")[0].status_code, 400)

    def test_quiz(self):
        self.assertEqual(self.get("/api/quiz")[1]["count"], 5)
        self.assertEqual(self.get("/api/quiz?n=50")[1]["count"], 7)
        self.assertEqual(self.get("/api/quiz?planet=Moon")[1]["count"], 3)
        self.assertEqual(self.get("/api/quiz?shuffle=true&n=3")[1]["count"], 3)
        self.assertEqual(self.get("/api/quiz?n=0")[0].status_code, 400)


class DataIntegrityTests(unittest.TestCase):
    def test_real_dataset_loads(self):
        store = load_dataset(BACKEND / "equipment.json")
        self.assertEqual(len(store["equipment"]), 7)
        questions = load_quiz(BACKEND / "quiz.json", store["by_id"])
        self.assertEqual(len(questions), 7)

    def test_only_left_on_surface_records_claim_deliberate_abandonment(self):
        for item in load_dataset(BACKEND / "equipment.json")["equipment"]:
            if item["status"] != "left_on_surface":
                self.assertNotIn("abandon", item["status_label"].lower())

    def test_no_sol_day_mixups(self):
        text = (BACKEND / "equipment.json").read_text(encoding="utf-8").lower()
        self.assertNotIn("83 days", text)
        self.assertNotIn("seven-day", text)

    def _broken(self, mutate):
        data = copy.deepcopy(RAW)
        mutate(data)
        return write_temp(data)

    def test_validation_rejects_bad_data(self):
        cases = {
            "unknown status": lambda d: d["equipment"][0].update(status="abandoned"),
            "bad date": lambda d: d["equipment"][0]["timeline"][0].update(date="1971-13-40"),
            "latitude out of range": lambda d: d["equipment"][0]["coordinates"].update(lat=123),
            "duplicate id": lambda d: d["equipment"][1].update(id=d["equipment"][0]["id"]),
            "year mismatch": lambda d: d["equipment"][0].update(year=1999),
            "missing field": lambda d: d["equipment"][0].pop("story"),
            "no sources": lambda d: d["equipment"][0].update(sources=[]),
            "http source": lambda d: d["equipment"][0]["sources"][0].update(url="http://example.com"),
            "operational mismatch": lambda d: d["equipment"][0].update(operational=True),
            "left_on_surface without departure": lambda d: d["equipment"][0].update(
                timeline=[e for e in d["equipment"][0]["timeline"] if e["kind"] != "departure"]),
        }
        for label, mutate in cases.items():
            with self.subTest(label), self.assertRaises(DataError):
                load_dataset(self._broken(mutate))

    def test_app_refuses_to_start_on_bad_data(self):
        path = self._broken(lambda d: d["equipment"][0].update(status="abandoned"))
        with self.assertRaises(DataError):
            create_app(equipment_file=path)

    def test_quiz_validation(self):
        store = load_dataset(BACKEND / "equipment.json")
        quiz = json.loads((BACKEND / "quiz.json").read_text(encoding="utf-8"))
        quiz["questions"][0]["correct_option_id"] = "z"
        with self.assertRaises(DataError):
            load_quiz(write_temp(quiz), store["by_id"])


if __name__ == "__main__":
    unittest.main()
