import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import server

MINI_FUELIO = b'''"## Vehicle"
"Name","Description","Make","Model","Year","VIN"
"Demo Vehicle","","Maker","Demo","2026","DO_NOT_STORE"
"## TripLog"
"guid","title","StartName","StartLat","StartLong","StartDate","EndName","EndLat","EndLong","EndDate","TripDist","TripDuration","TripCost","TripCostKm","TripAvgSpeed","TripTopSpeed"
"trip-1","Test drive","Start","19.1000","73.0000","2026-01-01 10:00","End","19.1050","73.0050","2026-01-01 10:10","1000","600","7","7","1.67","5"
"## Log"
"guid","Data","Odo (km)","Fuel (litres)","Full","Price (optional)","km/l (optional)"
"fuel-1","2026-01-01 10:15","500","20","1","2000","15.0"
'''
MINI_GPX = b'''<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
<trk><name>Test movement</name><trkseg>
<trkpt lat="19.1000" lon="73.0000"><time>2026-01-01T04:30:00Z</time><speed>5.0</speed><src>gps</src><sat>8</sat><hdop>1.2</hdop></trkpt>
<trkpt lat="19.1025" lon="73.0025"><time>2026-01-01T04:32:00Z</time><speed>6.0</speed><src>gps</src><sat>8</sat><hdop>1.2</hdop></trkpt>
<trkpt lat="19.1050" lon="73.0050"><time>2026-01-01T04:34:00Z</time><speed>4.0</speed><src>gps</src><sat>7</sat><hdop>1.4</hdop></trkpt>
</trkseg></trk></gpx>'''

class BlackBoxImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = server.DB_PATH
        server.DB_PATH = Path(self.tmp.name) / "test.sqlite3"
        server.init_db()
        self.db = server.connect_db()

    def tearDown(self):
        self.db.close()
        server.DB_PATH = self.old_db
        self.tmp.cleanup()

    def test_minimal_fuelio_import_normalizes_units(self):
        result = server.import_one(self.db, "minimal.csv", MINI_FUELIO)
        summary = server.get_summary(self.db)
        self.assertEqual(result["status"], "imported")
        self.assertEqual(summary["trips"]["count"], 1)
        self.assertAlmostEqual(summary["trips"]["distance_km"], 1.0)
        self.assertEqual(summary["fuel"]["count"], 1)
        # Vehicle metadata is intentionally limited; VIN and registration are not saved.
        vehicle = dict(self.db.execute("SELECT * FROM vehicle WHERE id=1").fetchone())
        self.assertNotIn("VIN", vehicle)
        self.assertNotIn("plate", vehicle)

    def test_minimal_gpx_import_and_candidate_detection(self):
        server.import_one(self.db, "minimal.gpx", MINI_GPX)
        summary = server.get_summary(self.db)
        self.assertEqual(summary["gps"]["files"], 1)
        self.assertEqual(summary["gps"]["points"], 3)
        candidates = server.get_detected_trips(self.db)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertGreater(candidates[0]["estimated_path_km"], 0.5)

    def test_duplicate_file_is_skipped(self):
        first = server.import_one(self.db, "minimal.gpx", MINI_GPX)
        second = server.import_one(self.db, "minimal.gpx", MINI_GPX)
        self.assertEqual(first["status"], "imported")
        self.assertEqual(second["status"], "already imported")


    def test_manual_fuel_entry_is_saved_and_efficiency_is_calculated_full_to_full(self):
        first = server.create_manual_fuel(self.db, {
            "date_local": "2026-01-01T12:00", "odometer_km": 1000,
            "litres": 20, "price_inr": 2000, "full_tank": True, "notes": "first fill"
        })
        second = server.create_manual_fuel(self.db, {
            "date_local": "2026-01-10T12:00", "odometer_km": 1300,
            "litres": 20, "price_inr": 2200, "full_tank": True
        })
        self.assertEqual(first["source_type"], "manual")
        self.assertIsNone(first["reported_km_per_l"])
        self.assertAlmostEqual(second["reported_km_per_l"], 15.0)
        summary = server.get_summary(self.db)
        self.assertEqual(summary["fuel"]["count"], 2)
        self.assertAlmostEqual(summary["fuel"]["cost_inr"], 4200.0)

    def test_partial_fill_prevents_full_to_full_efficiency_guess(self):
        server.create_manual_fuel(self.db, {
            "date_local": "2026-01-01T12:00", "odometer_km": 1000,
            "litres": 20, "price_inr": 2000, "full_tank": True
        })
        server.create_manual_fuel(self.db, {
            "date_local": "2026-01-05T12:00", "odometer_km": 1150,
            "litres": 8, "price_inr": 800, "full_tank": False
        })
        last = server.create_manual_fuel(self.db, {
            "date_local": "2026-01-10T12:00", "odometer_km": 1300,
            "litres": 20, "price_inr": 2200, "full_tank": True
        })
        self.assertIsNone(last["reported_km_per_l"])

    def test_manual_vehicle_cost_entry(self):
        entry = server.create_manual_cost(self.db, {
            "date_local": "2026-01-03T17:30", "title": "Toll",
            "cost_inr": 150, "odometer_km": 1200, "category_id": "Toll / parking"
        })
        self.assertEqual(entry["source_type"], "manual")
        self.assertEqual(entry["title"], "Toll")
        self.assertEqual(server.get_summary(self.db)["costs"]["total_inr"], 150)

    def test_manual_fuel_validation_rejects_zero_litres(self):
        with self.assertRaises(ValueError):
            server.create_manual_fuel(self.db, {
                "date_local": "2026-01-01T12:00", "litres": 0,
                "price_inr": 2000, "full_tank": True
            })

    def test_supplied_dataset_if_present(self):
        imports_dir = ROOT / "private-imports"
        files = sorted(p for p in imports_dir.iterdir() if p.suffix.lower() in (".csv", ".gpx"))
        if not files:
            self.skipTest("Private CSV/GPX starter files are intentionally absent from source-only package")
        for path in files:
            server.import_one(self.db, path.name, path.read_bytes())
        summary = server.get_summary(self.db)
        self.assertEqual(summary["trips"]["count"], 311)
        self.assertAlmostEqual(summary["trips"]["distance_km"], 5990.404, places=2)
        self.assertEqual(summary["fuel"]["count"], 3)
        self.assertEqual(summary["costs"]["count"], 1)
        self.assertEqual(summary["gps"]["files"], 4)
        self.assertEqual(summary["gps"]["points"], 1195)
        self.assertEqual(summary["gps"]["unique_points"], 1192)
        self.assertGreaterEqual(len(server.get_detected_trips(self.db)), 1)

if __name__ == "__main__":
    unittest.main()
