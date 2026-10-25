#!/usr/bin/env python3
"""Knowhere Black Box: GPSLogger GPX vehicle tracker with optional legacy CSV import.

Dependency-free Python 3 service. Designed for a private homelab deployment.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("BLACKBOX_DB", "/data/blackbox.sqlite3"))
IMPORT_DIR = Path(os.environ.get("BLACKBOX_IMPORT_DIR", "/imports"))
USERNAME = os.environ.get("BLACKBOX_USERNAME", "blackbox")
PASSWORD = os.environ.get("BLACKBOX_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8000"))
MAX_UPLOAD_BYTES = 80 * 1024 * 1024
DB_LOCK = threading.RLock()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


@contextmanager
def db_session():
    db = connect_db()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    with DB_LOCK, db_session() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS imports (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          filename TEXT NOT NULL,
          sha256 TEXT NOT NULL UNIQUE,
          kind TEXT NOT NULL,
          record_count INTEGER NOT NULL DEFAULT 0,
          details_json TEXT NOT NULL DEFAULT '{}',
          imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vehicle (
          id INTEGER PRIMARY KEY CHECK (id=1),
          name TEXT, make TEXT, model TEXT, year TEXT
        );
        CREATE TABLE IF NOT EXISTS vehicles (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          make TEXT, model TEXT, year TEXT,
          fuel_type TEXT NOT NULL DEFAULT 'petrol',
          km_per_l REAL,
          fuel_price_per_litre REAL,
          connection_name TEXT,
          ingest_key TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fuelio_trips (
          guid TEXT PRIMARY KEY,
          title TEXT, start_name TEXT, start_lat REAL, start_lon REAL,
          start_date TEXT, start_odo_km REAL,
          end_name TEXT, end_lat REAL, end_lon REAL, end_date TEXT,
          end_odo_km REAL, distance_m REAL, duration_s REAL,
          trip_cost_inr REAL, trip_cost_per_km REAL,
          avg_speed_mps REAL, top_speed_mps REAL, note TEXT,
          raw_json TEXT NOT NULL DEFAULT '{}', last_import_sha TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_fuelio_trips_start_date ON fuelio_trips(start_date);
        CREATE TABLE IF NOT EXISTS fuel_entries (
          guid TEXT PRIMARY KEY,
          date_local TEXT, odometer_km REAL, litres REAL, full_tank INTEGER,
          price_inr REAL, reported_km_per_l REAL,
          latitude REAL, longitude REAL, city TEXT, notes TEXT,
          source_type TEXT NOT NULL DEFAULT 'historical-import',
          raw_json TEXT NOT NULL DEFAULT '{}', last_import_sha TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_fuel_entries_date ON fuel_entries(date_local);
        CREATE TABLE IF NOT EXISTS cost_entries (
          guid TEXT PRIMARY KEY,
          title TEXT, date_local TEXT, odometer_km REAL, cost_inr REAL,
          category_id TEXT, notes TEXT,
          source_type TEXT NOT NULL DEFAULT 'historical-import',
          raw_json TEXT NOT NULL DEFAULT '{}', last_import_sha TEXT
        );
        CREATE TABLE IF NOT EXISTS gpx_tracks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          filename TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
          track_name TEXT, start_utc TEXT, end_utc TEXT,
          point_count INTEGER NOT NULL DEFAULT 0,
          source_counts_json TEXT NOT NULL DEFAULT '{}',
          speed_point_count INTEGER NOT NULL DEFAULT 0,
          imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gpx_points (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          track_id INTEGER NOT NULL REFERENCES gpx_tracks(id) ON DELETE CASCADE,
          point_index INTEGER NOT NULL,
          ts_utc TEXT NOT NULL,
          latitude REAL NOT NULL, longitude REAL NOT NULL,
          elevation_m REAL, speed_mps REAL,
          source TEXT NOT NULL DEFAULT 'unknown',
          satellites INTEGER, hdop REAL, vdop REAL, pdop REAL,
          UNIQUE(track_id, point_index)
        );
        CREATE INDEX IF NOT EXISTS ix_gpx_points_time ON gpx_points(ts_utc);
        """)
        # Compatible with any pre-manual-entry V1 database.
        for table in ("fuel_entries", "cost_entries"):
            columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
            if "source_type" not in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN source_type TEXT NOT NULL DEFAULT 'historical-import'")

        # Add vehicle identity to existing records without rebuilding the live database.
        for table in ("fuelio_trips", "fuel_entries", "cost_entries", "gpx_tracks"):
            columns = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
            if "vehicle_id" not in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN vehicle_id INTEGER REFERENCES vehicles(id)")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS live_drives (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
          started_utc TEXT NOT NULL,
          last_point_utc TEXT NOT NULL,
          ended_utc TEXT,
          last_moving_utc TEXT,
          start_lat REAL NOT NULL, start_lon REAL NOT NULL,
          end_lat REAL NOT NULL, end_lon REAL NOT NULL,
          distance_km REAL NOT NULL DEFAULT 0,
          moving_seconds REAL NOT NULL DEFAULT 0,
          max_speed_kmh REAL,
          speed_sum_kmh REAL NOT NULL DEFAULT 0,
          speed_count INTEGER NOT NULL DEFAULT 0,
          point_count INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'active',
          fuel_cost_estimate_inr REAL,
          fuel_litres_estimate REAL,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_live_drives_vehicle_start ON live_drives(vehicle_id, started_utc DESC);
        CREATE TABLE IF NOT EXISTS live_points (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          drive_id INTEGER REFERENCES live_drives(id) ON DELETE SET NULL,
          vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
          ts_utc TEXT NOT NULL,
          latitude REAL NOT NULL, longitude REAL NOT NULL,
          speed_mps REAL, accuracy_m REAL, elevation_m REAL, bearing_deg REAL,
          received_at TEXT NOT NULL,
          UNIQUE(vehicle_id, ts_utc)
        );
        CREATE INDEX IF NOT EXISTS ix_live_points_drive_time ON live_points(drive_id, ts_utc);
        """)
        # Create/repair two profiles. Existing historical CSV metadata may name the first profile.
        legacy = db.execute("SELECT name,make,model,year FROM vehicle WHERE id=1").fetchone()
        count = db.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
        if count == 0:
            now = now_utc()
            legacy_name = (legacy["name"] if legacy else None) or "Primary vehicle"
            db.execute("""INSERT INTO vehicles(name,make,model,year,fuel_type,ingest_key,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""", (legacy_name, legacy["make"] if legacy else None,
                legacy["model"] if legacy else None, legacy["year"] if legacy else None,
                "petrol", uuid.uuid4().hex + uuid.uuid4().hex, now, now))
            db.execute("""INSERT INTO vehicles(name,fuel_type,ingest_key,created_at,updated_at)
                VALUES(?,?,?,?,?)""", ("Second vehicle — configure me", "petrol",
                uuid.uuid4().hex + uuid.uuid4().hex, now, now))
        elif legacy:
            db.execute("""UPDATE vehicles SET name=COALESCE(NULLIF(?,''),name),make=COALESCE(NULLIF(?,''),make),
                model=COALESCE(NULLIF(?,''),model),year=COALESCE(NULLIF(?,''),year),updated_at=? WHERE id=1""",
                (legacy["name"], legacy["make"], legacy["model"], legacy["year"], now_utc()))
        # One-time legacy rows belong to the vehicle from the original Fuelio export.
        db.execute("UPDATE fuelio_trips SET vehicle_id=1 WHERE vehicle_id IS NULL")
        db.execute("UPDATE fuel_entries SET vehicle_id=1 WHERE vehicle_id IS NULL")
        db.execute("UPDATE cost_entries SET vehicle_id=1 WHERE vehicle_id IS NULL")
        # db_session closes the connection directly rather than committing on context exit.
        # Persist the idempotent profile seed and schema migrations before closing.
        db.commit()


def num(value, default=None):
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    try:
        out = float(s)
        if not math.isfinite(out):
            return default
        return out
    except (ValueError, TypeError):
        return default


def int_num(value, default=None):
    v = num(value, default)
    return int(v) if v is not None else default


def scrub_zero_coord(value):
    v = num(value)
    return None if v is None or abs(v) < 1e-9 else v


def parse_csv_sections(content: bytes) -> dict[str, list[dict[str, str]]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")
    sections: dict[str, list[dict[str, str]]] = {}
    current = None
    rows_by_section: dict[str, list[list[str]]] = {}
    for row in csv.reader(io.StringIO(text, newline="")):
        if len(row) == 1 and row[0].strip().startswith("## "):
            current = row[0].strip()[3:].strip()
            rows_by_section.setdefault(current, [])
        elif current and row and any(str(x).strip() for x in row):
            rows_by_section[current].append(row)
    for section, rows in rows_by_section.items():
        if not rows:
            sections[section] = []
            continue
        headers = [h.strip() for h in rows[0]]
        values = []
        for row in rows[1:]:
            padded = row + [""] * max(0, len(headers) - len(row))
            values.append({headers[i]: (padded[i] if i < len(padded) else "").strip() for i in range(len(headers))})
        sections[section] = values
    return sections


def parse_time_utc(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        return None


def local_time_key(value: str) -> str | None:
    if not value:
        return None
    value = value.strip()
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M").isoformat(timespec="minutes")
    except ValueError:
        try:
            return datetime.fromisoformat(value).isoformat(timespec="minutes")
        except ValueError:
            return None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def import_fuelio(db: sqlite3.Connection, filename: str, content: bytes, digest: str) -> dict:
    sections = parse_csv_sections(content)
    if not any(k in sections for k in ("TripLog", "Log", "Costs", "Vehicle")):
        raise ValueError("CSV does not look like a Fuelio export (expected sections such as ## TripLog or ## Log).")
    existing = db.execute("SELECT id FROM imports WHERE sha256=?", (digest,)).fetchone()
    if existing:
        return {"filename": filename, "kind": "Historical CSV (Fuelio format)", "status": "already imported", "records": 0}

    stats = {"trips": 0, "fuel_entries": 0, "cost_entries": 0, "vehicle_metadata": 0}
    with db:
        # Deliberately do not store VIN or plate from the Fuelio Vehicle section.
        vehicle_rows = sections.get("Vehicle", [])
        if vehicle_rows:
            v = vehicle_rows[0]
            db.execute("""INSERT INTO vehicle(id,name,make,model,year) VALUES(1,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,make=excluded.make,model=excluded.model,year=excluded.year""",
                (v.get("Name"), v.get("Make"), v.get("Model"), v.get("Year")))
            db.execute("""UPDATE vehicles SET name=COALESCE(NULLIF(?,''),name),make=COALESCE(NULLIF(?,''),make),
                model=COALESCE(NULLIF(?,''),model),year=COALESCE(NULLIF(?,''),year),updated_at=? WHERE id=1""",
                (v.get("Name"), v.get("Make"), v.get("Model"), v.get("Year"), now_utc()))
            stats["vehicle_metadata"] = 1

        for i, row in enumerate(sections.get("TripLog", [])):
            guid = row.get("guid") or f"{digest}:trip:{i}"
            start_lat, start_lon = scrub_zero_coord(row.get("StartLat")), scrub_zero_coord(row.get("StartLong"))
            end_lat, end_lon = scrub_zero_coord(row.get("EndLat")), scrub_zero_coord(row.get("EndLong"))
            db.execute("""INSERT INTO fuelio_trips(
              guid,title,start_name,start_lat,start_lon,start_date,start_odo_km,
              end_name,end_lat,end_lon,end_date,end_odo_km,distance_m,duration_s,
              trip_cost_inr,trip_cost_per_km,avg_speed_mps,top_speed_mps,note,raw_json,last_import_sha)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(guid) DO UPDATE SET title=excluded.title,start_name=excluded.start_name,
              start_lat=excluded.start_lat,start_lon=excluded.start_lon,start_date=excluded.start_date,
              start_odo_km=excluded.start_odo_km,end_name=excluded.end_name,end_lat=excluded.end_lat,
              end_lon=excluded.end_lon,end_date=excluded.end_date,end_odo_km=excluded.end_odo_km,
              distance_m=excluded.distance_m,duration_s=excluded.duration_s,trip_cost_inr=excluded.trip_cost_inr,
              trip_cost_per_km=excluded.trip_cost_per_km,avg_speed_mps=excluded.avg_speed_mps,
              top_speed_mps=excluded.top_speed_mps,note=excluded.note,raw_json=excluded.raw_json,
              last_import_sha=excluded.last_import_sha""",
              (guid, row.get("title"), row.get("StartName"), start_lat, start_lon,
               local_time_key(row.get("StartDate")), num(row.get("StartOdo")),
               row.get("EndName"), end_lat, end_lon, local_time_key(row.get("EndDate")),
               num(row.get("EndOdo")), num(row.get("TripDist")), num(row.get("TripDuration")),
               num(row.get("TripCost")), num(row.get("TripCostKm")), num(row.get("TripAvgSpeed")),
               num(row.get("TripTopSpeed")), row.get("TripNote"), json.dumps(row, ensure_ascii=False), digest))
            stats["trips"] += 1

        for i, row in enumerate(sections.get("Log", [])):
            guid = row.get("guid") or f"{digest}:fuel:{i}"
            db.execute("""INSERT INTO fuel_entries(guid,date_local,odometer_km,litres,full_tank,price_inr,
              reported_km_per_l,latitude,longitude,city,notes,raw_json,last_import_sha)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(guid) DO UPDATE SET date_local=excluded.date_local,odometer_km=excluded.odometer_km,
              litres=excluded.litres,full_tank=excluded.full_tank,price_inr=excluded.price_inr,
              reported_km_per_l=excluded.reported_km_per_l,latitude=excluded.latitude,longitude=excluded.longitude,
              city=excluded.city,notes=excluded.notes,raw_json=excluded.raw_json,last_import_sha=excluded.last_import_sha""",
              (guid, local_time_key(row.get("Data")), num(row.get("Odo (km)")), num(row.get("Fuel (litres)")),
               int_num(row.get("Full")), num(row.get("Price (optional)")), num(row.get("km/l (optional)")),
               scrub_zero_coord(row.get("latitude (optional)")), scrub_zero_coord(row.get("longitude (optional)")),
               row.get("City (optional)"), row.get("Notes (optional)"), json.dumps(row, ensure_ascii=False), digest))
            stats["fuel_entries"] += 1

        for i, row in enumerate(sections.get("Costs", [])):
            guid = row.get("guid") or f"{digest}:cost:{i}"
            db.execute("""INSERT INTO cost_entries(guid,title,date_local,odometer_km,cost_inr,category_id,notes,raw_json,last_import_sha)
              VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(guid) DO UPDATE SET title=excluded.title,date_local=excluded.date_local,
              odometer_km=excluded.odometer_km,cost_inr=excluded.cost_inr,category_id=excluded.category_id,
              notes=excluded.notes,raw_json=excluded.raw_json,last_import_sha=excluded.last_import_sha""",
              (guid, row.get("CostTitle"), local_time_key(row.get("Date")), num(row.get("Odo")),
               num(row.get("Cost")), row.get("CostTypeID"), row.get("Notes"), json.dumps(row, ensure_ascii=False), digest))
            stats["cost_entries"] += 1
        # The legacy CSV describes one vehicle (profile 1). Do not infer a vehicle for GPX tracks.
        db.execute("UPDATE fuelio_trips SET vehicle_id=1 WHERE last_import_sha=?", (digest,))
        db.execute("UPDATE fuel_entries SET vehicle_id=1 WHERE last_import_sha=?", (digest,))
        db.execute("UPDATE cost_entries SET vehicle_id=1 WHERE last_import_sha=?", (digest,))
        total = stats["trips"] + stats["fuel_entries"] + stats["cost_entries"]
        db.execute("INSERT INTO imports(filename,sha256,kind,record_count,details_json,imported_at) VALUES(?,?,?,?,?,?)",
                   (filename, digest, "Historical CSV (Fuelio format)", total, json.dumps(stats), now_utc()))
    return {"filename": filename, "kind": "Historical CSV (Fuelio format)", "status": "imported", "records": stats["trips"] + stats["fuel_entries"] + stats["cost_entries"], "details": stats}


def _valid_local_datetime(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("Date and time are required.")
    parsed = local_time_key(value)
    if not parsed:
        raise ValueError("Enter a valid local date and time.")
    # Keep user-entered local wall time; this app is operated in Asia/Kolkata.
    return parsed


def create_manual_fuel(db: sqlite3.Connection, payload: dict) -> dict:
    date_local = _valid_local_datetime(payload.get("date_local"))
    litres = num(payload.get("litres"))
    amount = num(payload.get("price_inr"))
    odometer = num(payload.get("odometer_km"))
    full_tank_raw = payload.get("full_tank", False)
    full_tank = 1 if full_tank_raw is True or str(full_tank_raw).lower() in ("1", "true", "yes", "on") else 0
    notes = str(payload.get("notes") or "").strip()[:1000]
    vehicle_id = int_num(payload.get("vehicle_id"), 1)
    if not db.execute("SELECT 1 FROM vehicles WHERE id=?", (vehicle_id,)).fetchone():
        raise ValueError("Choose a valid vehicle.")
    if litres is None or litres <= 0 or litres > 300:
        raise ValueError("Fuel volume must be greater than 0 and no more than 300 litres.")
    if amount is None or amount <= 0 or amount > 1000000:
        raise ValueError("Total amount must be greater than ₹0 and no more than ₹10,00,000.")
    if odometer is not None and (odometer < 0 or odometer > 10000000):
        raise ValueError("Odometer must be a non-negative value in kilometres.")
    efficiency = None
    if full_tank and odometer is not None:
        prev = db.execute("""SELECT odometer_km,date_local FROM fuel_entries
            WHERE vehicle_id=? AND full_tank=1 AND odometer_km IS NOT NULL AND date_local < ?
            ORDER BY date_local DESC LIMIT 1""", (vehicle_id, date_local)).fetchone()
        if prev and odometer > float(prev["odometer_km"]) and litres > 0:
            partial_between = db.execute("""SELECT COUNT(*) FROM fuel_entries
                WHERE vehicle_id=? AND full_tank=0 AND date_local > ? AND date_local < ?""",
                (vehicle_id, prev["date_local"], date_local)).fetchone()[0]
            # If any partial refuelling occurred since the previous full tank, the current
            # fill's litres alone cannot support a full-to-full efficiency calculation.
            if partial_between == 0:
                candidate = (odometer - float(prev["odometer_km"])) / litres
                if 0 < candidate < 100:
                    efficiency = round(candidate, 3)
    guid = "manual:" + uuid.uuid4().hex
    db.execute("""INSERT INTO fuel_entries(
        guid,date_local,odometer_km,litres,full_tank,price_inr,reported_km_per_l,
        latitude,longitude,city,notes,source_type,raw_json,last_import_sha,vehicle_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?)""",
        (guid,date_local,odometer,litres,full_tank,amount,efficiency,None,None,None,notes,
         "manual",json.dumps({"source":"Knowhere Black Box manual entry"}), vehicle_id))
    with db:
        row = db.execute("SELECT * FROM fuel_entries WHERE guid=?", (guid,)).fetchone()
    result = dict(row)
    result.pop("raw_json", None); result.pop("last_import_sha", None)
    return result


def create_manual_cost(db: sqlite3.Connection, payload: dict) -> dict:
    date_local = _valid_local_datetime(payload.get("date_local"))
    title = str(payload.get("title") or "").strip()[:160]
    amount = num(payload.get("cost_inr"))
    odometer = num(payload.get("odometer_km"))
    category = str(payload.get("category_id") or "Other").strip()[:80] or "Other"
    notes = str(payload.get("notes") or "").strip()[:1000]
    vehicle_id = int_num(payload.get("vehicle_id"), 1)
    if not db.execute("SELECT 1 FROM vehicles WHERE id=?", (vehicle_id,)).fetchone():
        raise ValueError("Choose a valid vehicle.")
    if not title:
        raise ValueError("Expense description is required.")
    if amount is None or amount <= 0 or amount > 1000000:
        raise ValueError("Amount must be greater than ₹0 and no more than ₹10,00,000.")
    if odometer is not None and (odometer < 0 or odometer > 10000000):
        raise ValueError("Odometer must be a non-negative value in kilometres.")
    guid = "manual:" + uuid.uuid4().hex
    db.execute("""INSERT INTO cost_entries(
        guid,title,date_local,odometer_km,cost_inr,category_id,notes,source_type,raw_json,last_import_sha,vehicle_id)
        VALUES(?,?,?,?,?,?,?,?,?,NULL,?)""",
        (guid,title,date_local,odometer,amount,category,notes,"manual",
         json.dumps({"source":"Knowhere Black Box manual entry"}), vehicle_id))
    with db:
        row = db.execute("SELECT * FROM cost_entries WHERE guid=?", (guid,)).fetchone()
    result = dict(row)
    result.pop("raw_json", None); result.pop("last_import_sha", None)
    return result


def child_text(elem, local_name, default=None):
    for child in list(elem):
        if child.tag.rsplit("}", 1)[-1] == local_name:
            return child.text.strip() if child.text and child.text.strip() else default
    return default


def import_gpx(db: sqlite3.Connection, filename: str, content: bytes, digest: str, vehicle_id: int | None = None) -> dict:
    existing = db.execute("SELECT id FROM gpx_tracks WHERE sha256=?", (digest,)).fetchone()
    if existing:
        return {"filename": filename, "kind": "GPSLogger GPX", "status": "already imported", "records": 0}
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("GPX is too large.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"GPX XML is invalid: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].lower() != "gpx":
        raise ValueError("XML root is not GPX.")

    name = None
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] == "trk":
            name = child_text(elem, "name") or name
            if name:
                break
    pts = []
    sources = Counter()
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] != "trkpt":
            continue
        lat, lon = num(elem.attrib.get("lat")), num(elem.attrib.get("lon"))
        ts = parse_time_utc(child_text(elem, "time"))
        if lat is None or lon is None or ts is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        src = (child_text(elem, "src", "unknown") or "unknown").strip().lower()
        sources[src] += 1
        pts.append({
          "point_index": len(pts), "ts_utc": ts, "latitude": lat, "longitude": lon,
          "elevation_m": num(child_text(elem, "ele")), "speed_mps": num(child_text(elem, "speed")),
          "source": src, "satellites": int_num(child_text(elem, "sat")),
          "hdop": num(child_text(elem, "hdop")), "vdop": num(child_text(elem, "vdop")), "pdop": num(child_text(elem, "pdop"))
        })
    if not pts:
        raise ValueError("No valid timestamped track points were found in this GPX.")
    pts.sort(key=lambda p: p["ts_utc"])
    for idx, point in enumerate(pts):
        point["point_index"] = idx
    start_utc, end_utc = pts[0]["ts_utc"], pts[-1]["ts_utc"]
    speed_count = sum(1 for p in pts if p["speed_mps"] is not None)
    with db:
        cur = db.execute("""INSERT INTO gpx_tracks(filename,sha256,track_name,start_utc,end_utc,point_count,source_counts_json,speed_point_count,imported_at,vehicle_id)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (filename, digest, name or Path(filename).stem, start_utc, end_utc, len(pts), json.dumps(sources), speed_count, now_utc(), vehicle_id))
        track_id = cur.lastrowid
        db.executemany("""INSERT INTO gpx_points(track_id,point_index,ts_utc,latitude,longitude,elevation_m,speed_mps,source,satellites,hdop,vdop,pdop)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          [(track_id,p["point_index"],p["ts_utc"],p["latitude"],p["longitude"],p["elevation_m"],p["speed_mps"],p["source"],p["satellites"],p["hdop"],p["vdop"],p["pdop"]) for p in pts])
        db.execute("INSERT INTO imports(filename,sha256,kind,record_count,details_json,imported_at) VALUES(?,?,?,?,?,?)",
                   (filename,digest,"GPSLogger GPX",len(pts),json.dumps({"points":len(pts),"sources":dict(sources),"speed_points":speed_count}),now_utc()))
    return {"filename": filename, "kind": "GPSLogger GPX", "status": "imported", "records": len(pts), "details": {"track_id": track_id, "points": len(pts), "sources": dict(sources), "speed_points": speed_count}}


def import_one(db: sqlite3.Connection, filename: str, content: bytes, vehicle_id: int | None = None) -> dict:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("File exceeds 80 MB upload limit.")
    digest = sha256(content)
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return import_fuelio(db, Path(filename).name, content, digest)
    if suffix == ".gpx":
        return import_gpx(db, Path(filename).name, content, digest, vehicle_id)
    raise ValueError("Unsupported file type. Use a GPSLogger .gpx track or an optional historical CSV export.")


def seed_imports() -> None:
    if not IMPORT_DIR.exists():
        return
    with DB_LOCK, db_session() as db:
        count = db.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    if count:
        return
    candidates = sorted([p for p in IMPORT_DIR.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".gpx")])
    for path in candidates:
        try:
            with DB_LOCK, db_session() as db:
                result = import_one(db, path.name, path.read_bytes())
            print(f"[seed] {path.name}: {result.get('status')} ({result.get('records', 0)} records)", flush=True)
        except Exception as exc:
            print(f"[seed] ERROR in {path.name}: {exc}", flush=True)


def meters_between(a, b) -> float:
    lat1, lon1 = math.radians(a["latitude"]), math.radians(a["longitude"])
    lat2, lon2 = math.radians(b["latitude"]), math.radians(b["longitude"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def vehicle_cost_model(db: sqlite3.Connection, vehicle: sqlite3.Row | dict) -> dict:
    """Return explicit model values when configured, otherwise learn from the vehicle's fuel ledger."""
    v = dict(vehicle)
    efficiency = num(v.get("km_per_l"))
    efficiency_source = "configured" if efficiency else None
    if not efficiency:
        recorded = [float(r[0]) for r in db.execute(
            "SELECT reported_km_per_l FROM fuel_entries WHERE vehicle_id=? AND reported_km_per_l BETWEEN 3 AND 60 ORDER BY date_local DESC LIMIT 12",
            (v["id"],)).fetchall() if r[0] is not None]
        if recorded:
            recorded.sort()
            efficiency = recorded[len(recorded)//2] if len(recorded) % 2 else (recorded[len(recorded)//2-1]+recorded[len(recorded)//2])/2
            efficiency_source = "fuel log median"
    price = num(v.get("fuel_price_per_litre"))
    price_source = "configured" if price else None
    if not price:
        ratios = [float(r[0])/float(r[1]) for r in db.execute(
            "SELECT price_inr,litres FROM fuel_entries WHERE vehicle_id=? AND price_inr>0 AND litres>0 AND price_inr/litres BETWEEN 1 AND 300 ORDER BY date_local DESC LIMIT 5",
            (v["id"],)).fetchall()]
        if ratios:
            price = ratios[0]
            price_source = "latest recorded fill-up"
    rate = (price / efficiency) if price and efficiency and efficiency > 0 else None
    return {
        "effective_km_per_l": round(efficiency, 3) if efficiency else None,
        "efficiency_source": efficiency_source,
        "effective_fuel_price_per_litre": round(price, 3) if price else None,
        "fuel_price_source": price_source,
        "estimated_cost_per_km_inr": round(rate, 3) if rate else None,
    }


def _vehicle_public(row: sqlite3.Row | dict, db: sqlite3.Connection | None = None) -> dict:
    out = dict(row)
    if db is not None:
        out.update(vehicle_cost_model(db, out))
    else:
        efficiency = num(out.get("km_per_l"))
        price = num(out.get("fuel_price_per_litre"))
        out["effective_km_per_l"] = efficiency
        out["efficiency_source"] = "configured" if efficiency else None
        out["effective_fuel_price_per_litre"] = price
        out["fuel_price_source"] = "configured" if price else None
        out["estimated_cost_per_km_inr"] = round(price/efficiency,3) if price and efficiency else None
    return out

def save_vehicle(db: sqlite3.Connection, payload: dict) -> dict:
    name = str(payload.get("name") or "").strip()[:100]
    if not name:
        raise ValueError("Vehicle name is required.")
    vehicle_id = int_num(payload.get("id"))
    make = str(payload.get("make") or "").strip()[:80] or None
    model = str(payload.get("model") or "").strip()[:100] or None
    year = str(payload.get("year") or "").strip()[:10] or None
    fuel_type = str(payload.get("fuel_type") or "petrol").strip().lower()
    if fuel_type not in ("petrol", "diesel", "hybrid", "cng", "ev", "other"):
        raise ValueError("Choose a supported fuel type.")
    km_per_l = num(payload.get("km_per_l"))
    fuel_price = num(payload.get("fuel_price_per_litre"))
    if km_per_l is not None and not (0.1 <= km_per_l <= 150):
        raise ValueError("Efficiency must be between 0.1 and 150 km/L.")
    if fuel_price is not None and not (0.1 <= fuel_price <= 1000):
        raise ValueError("Fuel price must be between ₹0.10 and ₹1,000 per unit.")
    connection_name = str(payload.get("connection_name") or "").strip()[:120] or None
    if connection_name:
        duplicate = db.execute("SELECT name FROM vehicles WHERE lower(connection_name)=lower(?) AND id<>COALESCE(?, -1) LIMIT 1", (connection_name, vehicle_id)).fetchone()
        if duplicate:
            raise ValueError(f"Bluetooth/head-unit name is already assigned to {duplicate['name']}. Each car must have a distinct connection name for automatic selection.")
    now = now_utc()
    with db:
        if vehicle_id:
            current = db.execute("SELECT id,ingest_key,created_at FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
            if not current:
                raise ValueError("Vehicle profile not found.")
            db.execute("""UPDATE vehicles SET name=?,make=?,model=?,year=?,fuel_type=?,km_per_l=?,fuel_price_per_litre=?,connection_name=?,updated_at=? WHERE id=?""",
                (name,make,model,year,fuel_type,km_per_l,fuel_price,connection_name,now,vehicle_id))
        else:
            cur = db.execute("""INSERT INTO vehicles(name,make,model,year,fuel_type,km_per_l,fuel_price_per_litre,connection_name,ingest_key,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (name,make,model,year,fuel_type,km_per_l,fuel_price,connection_name,uuid.uuid4().hex+uuid.uuid4().hex,now,now))
            vehicle_id = cur.lastrowid
        row = db.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        model = vehicle_cost_model(db, row)
        eff = model["effective_km_per_l"]
        rate = model["estimated_cost_per_km_inr"]
        if eff and rate:
            db.execute("UPDATE live_drives SET fuel_litres_estimate=distance_km/? , fuel_cost_estimate_inr=distance_km*? WHERE vehicle_id=?",
                       (eff, rate, vehicle_id))
        else:
            db.execute("UPDATE live_drives SET fuel_litres_estimate=NULL,fuel_cost_estimate_inr=NULL WHERE vehicle_id=?", (vehicle_id,))
    return _vehicle_public(row, db)


def _parse_external_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if re.fullmatch(r"\d{9,13}(?:\.\d+)?", raw):
        try:
            epoch = float(raw)
            if epoch > 10_000_000_000:
                epoch /= 1000
            return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except (ValueError, OverflowError, OSError):
            return None
    return parse_time_utc(raw)


def ingest_location(db: sqlite3.Connection, qs: dict[str, list[str]]) -> dict:
    key = (qs.get("key") or [""])[0]
    vehicle = db.execute("SELECT * FROM vehicles WHERE ingest_key=?", (key,)).fetchone() if key else None
    if not vehicle:
        raise PermissionError("Unknown or missing vehicle ingestion key.")
    def q(*names):
        for name in names:
            vals = qs.get(name)
            if vals and vals[0] != "":
                return vals[0]
        return None
    lat, lon = num(q("lat", "latitude")), num(q("lon", "long", "longitude"))
    ts = _parse_external_timestamp(q("timestamp", "ts", "time"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Valid lat and lon are required.")
    if ts is None:
        raise ValueError("A valid GPS timestamp is required.")
    speed = num(q("speed", "spd"))
    if speed is not None and (speed < 0 or speed > 90):
        speed = None
    accuracy = num(q("accuracy", "acc"))
    altitude = num(q("altitude", "alt"))
    bearing = num(q("bearing", "dir"))
    vehicle_id = int(vehicle["id"])
    now = datetime.now(timezone.utc)
    ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    with db:
        # Close stale open sessions before associating the next moving point.
        for active in db.execute("SELECT id,last_point_utc FROM live_drives WHERE vehicle_id=? AND status='active'", (vehicle_id,)).fetchall():
            last_dt = datetime.fromisoformat(active["last_point_utc"].replace("Z", "+00:00"))
            if (ts_dt-last_dt).total_seconds() > 12*60:
                db.execute("UPDATE live_drives SET status='completed',ended_utc=COALESCE(last_moving_utc,last_point_utc) WHERE id=?", (active["id"],))
        latest_active = db.execute("SELECT id,last_point_utc FROM live_drives WHERE vehicle_id=? AND status='active' ORDER BY id DESC LIMIT 1", (vehicle_id,)).fetchone()
        if latest_active:
            latest_dt = datetime.fromisoformat(latest_active["last_point_utc"].replace("Z", "+00:00"))
            if ts_dt < latest_dt:
                return {"ok": True, "ignored_out_of_order": True, "vehicle_id": vehicle_id}
        cur = db.execute("""INSERT OR IGNORE INTO live_points(vehicle_id,ts_utc,latitude,longitude,speed_mps,accuracy_m,elevation_m,bearing_deg,received_at)
            VALUES(?,?,?,?,?,?,?,?,?)""", (vehicle_id,ts,lat,lon,speed,accuracy,altitude,bearing,now_utc()))
        if cur.rowcount == 0:
            return {"ok": True, "duplicate": True, "vehicle_id": vehicle_id}
        active = db.execute("SELECT * FROM live_drives WHERE vehicle_id=? AND status='active' ORDER BY id DESC LIMIT 1", (vehicle_id,)).fetchone()
        speed_kmh = speed*3.6 if speed is not None else None
        drive_id = None
        if active is None:
            # Start only when movement is plausible. Stored GPS samples at walking speed stay unassigned.
            if speed_kmh is not None and speed_kmh >= 5:
                cur = db.execute("""INSERT INTO live_drives(vehicle_id,started_utc,last_point_utc,last_moving_utc,start_lat,start_lon,end_lat,end_lon,
                  distance_km,moving_seconds,max_speed_kmh,speed_sum_kmh,speed_count,point_count,status,created_at)
                  VALUES(?,?,?,?,?,?,?,?,0,0,?,?,1,1,'active',?)""",
                  (vehicle_id,ts,ts,ts,lat,lon,lat,lon,speed_kmh,speed_kmh,now_utc()))
                drive_id = cur.lastrowid
                db.execute("UPDATE live_points SET drive_id=? WHERE vehicle_id=? AND ts_utc=?", (drive_id,vehicle_id,ts))
        else:
            drive_id = int(active["id"])
            prev = db.execute("SELECT * FROM live_points WHERE drive_id=? AND id<>? ORDER BY ts_utc DESC LIMIT 1", (drive_id,cur.lastrowid)).fetchone()
            step_km = 0.0
            delta_s = 0.0
            if prev:
                prev_dt = datetime.fromisoformat(prev["ts_utc"].replace("Z", "+00:00"))
                delta_s = max(0.0,(ts_dt-prev_dt).total_seconds())
                step_m = meters_between({"latitude":prev["latitude"],"longitude":prev["longitude"]},{"latitude":lat,"longitude":lon})
                plausible = delta_s > 0 and delta_s <= 120 and step_m <= min(2500, max(150, delta_s*65))
                if plausible and (accuracy is None or accuracy <= 120) and (prev["accuracy_m"] is None or prev["accuracy_m"] <= 120):
                    step_km = step_m/1000.0
            moving = speed_kmh is not None and speed_kmh >= 4
            new_distance = float(active["distance_km"] or 0) + step_km
            moving_seconds = float(active["moving_seconds"] or 0) + (delta_s if moving and delta_s <= 120 else 0)
            max_speed = max([x for x in (active["max_speed_kmh"], speed_kmh if moving else None) if x is not None], default=None)
            speed_sum = float(active["speed_sum_kmh"] or 0)+(speed_kmh if moving else 0)
            speed_count = int(active["speed_count"] or 0)+(1 if moving else 0)
            model = vehicle_cost_model(db, vehicle)
            km_per_l = model["effective_km_per_l"]
            cost_rate = model["estimated_cost_per_km_inr"]
            litres_est = new_distance/float(km_per_l) if km_per_l else None
            cost_est = new_distance*float(cost_rate) if cost_rate is not None else None
            last_moving_utc = ts if moving else active["last_moving_utc"]
            db.execute("""UPDATE live_drives SET last_point_utc=?,last_moving_utc=?,end_lat=?,end_lon=?,distance_km=?,moving_seconds=?,max_speed_kmh=?,
                speed_sum_kmh=?,speed_count=?,point_count=point_count+1,fuel_litres_estimate=?,fuel_cost_estimate_inr=? WHERE id=?""",
                (ts,last_moving_utc,lat,lon,new_distance,moving_seconds,max_speed,speed_sum,speed_count,litres_est,cost_est,drive_id))
            db.execute("UPDATE live_points SET drive_id=? WHERE id=?", (drive_id,cur.lastrowid))
            if not moving and active["last_moving_utc"]:
                lastmove = datetime.fromisoformat(active["last_moving_utc"].replace("Z", "+00:00"))
                if (ts_dt-lastmove).total_seconds() >= 12*60:
                    db.execute("UPDATE live_drives SET status='completed',ended_utc=? WHERE id=?", (active["last_moving_utc"],drive_id))
        return {"ok": True, "duplicate": False, "vehicle_id": vehicle_id, "drive_id": drive_id}


def get_vehicle_list(db: sqlite3.Connection) -> list[dict]:
    vehicles = fetch_all(db, "SELECT * FROM vehicles ORDER BY id")
    for v in vehicles:
        v.update(vehicle_cost_model(db, v))
        last = db.execute("SELECT MAX(received_at) FROM live_points WHERE vehicle_id=?",(v["id"],)).fetchone()[0]
        v["last_point_received_at"] = last
        active = db.execute("SELECT id,started_utc,last_point_utc,distance_km,status FROM live_drives WHERE vehicle_id=? AND status='active' ORDER BY id DESC LIMIT 1", (v["id"],)).fetchone()
        v["active_drive"] = dict(active) if active else None
    return vehicles


def get_live_drives(db: sqlite3.Connection) -> list[dict]:
    # A stopped GPSLogger session sends no final point. Close stale drives from wall-clock time
    # so the UI does not leave a disconnected car marked ACTIVE forever.
    wall_now = datetime.now(timezone.utc)
    active_rows = fetch_all(db, "SELECT id,last_point_utc,last_moving_utc FROM live_drives WHERE status='active'")
    with db:
        for active in active_rows:
            try:
                last_point = datetime.fromisoformat(active["last_point_utc"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if (wall_now - last_point).total_seconds() > 12 * 60:
                db.execute("UPDATE live_drives SET status='completed',ended_utc=COALESCE(last_moving_utc,last_point_utc) WHERE id=? AND status='active'", (active["id"],))
    rows = fetch_all(db, """SELECT d.*,v.name AS vehicle_name,v.km_per_l,v.fuel_price_per_litre,v.id AS selected_vehicle_id
        FROM live_drives d JOIN vehicles v ON v.id=d.vehicle_id ORDER BY d.started_utc DESC LIMIT 300""")
    models = {v["id"]: vehicle_cost_model(db, v) for v in fetch_all(db, "SELECT * FROM vehicles")}
    for d in rows:
        d["duration_min"] = round(max(0,(datetime.fromisoformat((d["ended_utc"] or d["last_point_utc"]).replace("Z","+00:00"))-datetime.fromisoformat(d["started_utc"].replace("Z","+00:00"))).total_seconds()/60),1)
        d["avg_moving_speed_kmh"] = round(d["speed_sum_kmh"]/d["speed_count"],1) if d.get("speed_count") else None
        model = models.get(d["vehicle_id"], {})
        eff, rate = model.get("effective_km_per_l"), model.get("estimated_cost_per_km_inr")
        d["effective_km_per_l"] = eff
        d["estimated_fuel_litres"] = round(d["distance_km"]/eff,3) if eff and d["distance_km"] > 0 else None
        d["estimated_fuel_cost_inr"] = round(d["distance_km"]*rate,2) if rate is not None and d["distance_km"] > 0 else None
        d["estimate_basis"] = {"efficiency_source":model.get("efficiency_source"),"fuel_price_source":model.get("fuel_price_source")}
    return rows

def fetch_all(db, sql, params=()):
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def get_summary(db) -> dict:
    trip = db.execute("""SELECT COUNT(*) n, SUM(CASE WHEN COALESCE(distance_m,0)>0 THEN 1 ELSE 0 END) usable,
      SUM(COALESCE(distance_m,0))/1000.0 km, AVG(CASE WHEN distance_m>0 THEN distance_m END)/1000.0 avg_km,
      MIN(start_date) first, MAX(start_date) last,
      SUM(CASE WHEN COALESCE(distance_m,0)=0 THEN 1 ELSE 0 END) zero_dist,
      SUM(CASE WHEN COALESCE(distance_m,0)>100000 THEN 1 ELSE 0 END) over_100km,
      SUM(CASE WHEN COALESCE(start_odo_km,0)=0 AND COALESCE(end_odo_km,0)=0 THEN 1 ELSE 0 END) zero_odo
      FROM fuelio_trips""").fetchone()
    fuel = db.execute("SELECT COUNT(*) n, SUM(COALESCE(litres,0)) litres, SUM(COALESCE(price_inr,0)) cost, AVG(reported_km_per_l) reported_eff FROM fuel_entries").fetchone()
    costs = db.execute("SELECT COUNT(*) n, SUM(COALESCE(cost_inr,0)) total FROM cost_entries").fetchone()
    tracks = db.execute("SELECT COUNT(*) n, SUM(point_count) points, SUM(speed_point_count) speed_points, MIN(start_utc) first, MAX(end_utc) last FROM gpx_tracks").fetchone()
    unique_points = db.execute("SELECT COUNT(*) FROM (SELECT ts_utc,ROUND(latitude,6) lat,ROUND(longitude,6) lon,source FROM gpx_points GROUP BY ts_utc,ROUND(latitude,6),ROUND(longitude,6),source)").fetchone()[0]
    gpx_sources = defaultdict(int)
    for r in db.execute("SELECT source,COUNT(*) n FROM gpx_points GROUP BY source"):
        gpx_sources[r["source"]] = r["n"]
    monthly_rows = db.execute("""SELECT substr(start_date,1,7) month,COUNT(*) trips,SUM(COALESCE(distance_m,0))/1000.0 km
      FROM fuelio_trips WHERE start_date IS NOT NULL GROUP BY substr(start_date,1,7) ORDER BY month""").fetchall()
    imports = fetch_all(db, "SELECT filename,kind,record_count,imported_at,sha256 FROM imports ORDER BY id DESC")
    return {
      "vehicle": dict(db.execute("SELECT name,make,model,year FROM vehicle WHERE id=1").fetchone() or {}),
      "trips": {"count":trip["n"],"usable":trip["usable"] or 0,"distance_km":round(trip["km"] or 0,2),"average_km":round(trip["avg_km"] or 0,2),"first":trip["first"],"last":trip["last"],"zero_distance":trip["zero_dist"] or 0,"over_100km":trip["over_100km"] or 0,"zero_odometer":trip["zero_odo"] or 0},
      "fuel": {"count":fuel["n"],"litres":round(fuel["litres"] or 0,2),"cost_inr":round(fuel["cost"] or 0,2),"recorded_efficiency":round(fuel["reported_eff"],2) if fuel["reported_eff"] is not None else None},
      "costs":{"count":costs["n"],"total_inr":round(costs["total"] or 0,2)},
      "gps":{"files":tracks["n"],"points":tracks["points"] or 0,"unique_points":unique_points,"speed_points":tracks["speed_points"] or 0,"first":tracks["first"],"last":tracks["last"],"sources":dict(gpx_sources)},
      "monthly":[{"month":r["month"],"trips":r["trips"],"distance_km":round(r["km"] or 0,2)} for r in monthly_rows],
      "imports":imports
    }


def get_detected_trips(db) -> list[dict]:
    rows = fetch_all(db, """SELECT p.*,t.filename FROM gpx_points p JOIN gpx_tracks t ON t.id=p.track_id
      WHERE LOWER(p.source) IN ('gps','fused','gps/fused') AND p.speed_mps IS NOT NULL ORDER BY p.ts_utc,p.id""")
    # Collapse points duplicated by overlapping exports; prefer entries with richer telemetry.
    unique = {}
    for p in rows:
        k=(p["ts_utc"],round(p["latitude"],7),round(p["longitude"],7),p["source"])
        if k not in unique or ((p["speed_mps"] is not None) + (p["satellites"] is not None) + (p["hdop"] is not None)) > ((unique[k]["speed_mps"] is not None) + (unique[k]["satellites"] is not None) + (unique[k]["hdop"] is not None)):
            unique[k]=p
    moving=sorted([p for p in unique.values() if p["speed_mps"] is not None and 1.5 <= p["speed_mps"] <= 55],key=lambda p:p["ts_utc"])
    groups=[]
    for p in moving:
        dt=datetime.fromisoformat(p["ts_utc"].replace("Z","+00:00"))
        if not groups:
            groups.append([p]); continue
        prev=datetime.fromisoformat(groups[-1][-1]["ts_utc"].replace("Z","+00:00"))
        if (dt-prev).total_seconds() <= 6*60:
            groups[-1].append(p)
        else:
            groups.append([p])
    results=[]
    for group in groups:
        if len(group)<3: continue
        start=datetime.fromisoformat(group[0]["ts_utc"].replace("Z","+00:00"))
        end=datetime.fromisoformat(group[-1]["ts_utc"].replace("Z","+00:00"))
        duration=(end-start).total_seconds()
        if duration<120: continue
        path_m=sum(meters_between(a,b) for a,b in zip(group,group[1:]))
        if path_m<500: continue
        speeds=[p["speed_mps"]*3.6 for p in group if p["speed_mps"] is not None]
        results.append({
          "id":len(results)+1,"start_utc":group[0]["ts_utc"],"end_utc":group[-1]["ts_utc"],
          "duration_min":round(duration/60,1),"estimated_path_km":round(path_m/1000,2),
          "max_speed_kmh":round(max(speeds),1),"avg_moving_speed_kmh":round(sum(speeds)/len(speeds),1),
          "points":len(group),"source_files":sorted({p["filename"] for p in group}),
          "coordinates":[[p["latitude"],p["longitude"],p["ts_utc"],p["speed_mps"]*3.6] for p in group]
        })
    return results


def get_quality(db) -> list[dict]:
    issues=[]
    s=get_summary(db)
    if s["trips"]["zero_distance"]:
        issues.append({"level":"warning","title":f"{s['trips']['zero_distance']} historical trip rows have zero distance","detail":"Kept from the one-time historical import. These records may be incomplete; they are excluded from the usable-distance KPI."})
    if s["trips"]["over_100km"]:
        issues.append({"level":"review","title":f"{s['trips']['over_100km']} trips exceed 100 km","detail":"This may be legitimate for longer journeys. Review outliers before using any derived statistics."})
    if s["trips"]["zero_odometer"]:
        issues.append({"level":"warning","title":"Historical trip odometer fields are zero","detail":"The archived trip summaries do not contain usable start/end odometer readings. Log the odometer in Black Box when recording future refuelling."})
    if s["fuel"]["count"] and s["trips"]["last"]:
        latest_fuel=db.execute("SELECT MAX(date_local) FROM fuel_entries").fetchone()[0]
        if latest_fuel and latest_fuel < s["trips"]["last"]:
            issues.append({"level":"warning","title":"Fuel ledger ends before archived trip history","detail":f"Latest fuel entry: {latest_fuel}; latest archived trip: {s['trips']['last']}. New fill-ups can be recorded directly in Black Box."})
    if s["gps"]["points"]:
        gaps=[]
        rows=fetch_all(db,"SELECT ts_utc FROM gpx_points ORDER BY ts_utc")
        last=None
        for r in rows:
            now=datetime.fromisoformat(r["ts_utc"].replace("Z","+00:00"))
            if last:
                gaps.append((now-last).total_seconds())
            last=now
        if gaps:
            med=sorted(gaps)[len(gaps)//2]
            if med>15:
                issues.append({"level":"warning","title":f"GPS logging is sparse (median interval {int(med)} s across stored points)","detail":"Current GPX exports include long gaps and network-derived points. Candidate trips are heuristic, not official trip records. Increase logging frequency before relying on detailed replay."})
        if s["gps"]["speed_points"] < s["gps"]["points"] * .25:
            issues.append({"level":"info","title":"Only some GPX points include speed telemetry","detail":"Speed is present where GPSLogger wrote it. Missing speed values are left unknown rather than inferred as zero."})
    return issues


def api_payload(db, parsed) -> tuple[int, object, str]:
    path = parsed.path
    qs = parse_qs(parsed.query)
    if path == "/api/health":
        return 200,{"ok":True,"service":"knowhere-blackbox"},"application/json"
    if path == "/api/vehicles":
        return 200,get_vehicle_list(db),"application/json"
    if path == "/api/live-drives":
        return 200,get_live_drives(db),"application/json"
    if path.startswith("/api/live-drives/"):
        try: drive_id=int(path.rsplit("/",1)[1])
        except ValueError: return 400,{"error":"Invalid drive id"},"application/json"
        drive=db.execute("""SELECT d.*,v.name AS vehicle_name,v.km_per_l,v.fuel_price_per_litre FROM live_drives d JOIN vehicles v ON v.id=d.vehicle_id WHERE d.id=?""",(drive_id,)).fetchone()
        if not drive:return 404,{"error":"Drive not found"},"application/json"
        points=fetch_all(db,"SELECT ts_utc,latitude,longitude,speed_mps,accuracy_m,elevation_m,bearing_deg FROM live_points WHERE drive_id=? ORDER BY ts_utc",(drive_id,))
        result=dict(drive); result["points"]=points
        result["avg_moving_speed_kmh"]=round(result["speed_sum_kmh"]/result["speed_count"],1) if result.get("speed_count") else None
        vehicle_row=db.execute("SELECT * FROM vehicles WHERE id=?",(result["vehicle_id"],)).fetchone()
        model=vehicle_cost_model(db,vehicle_row)
        result["effective_km_per_l"]=model["effective_km_per_l"]
        result["estimated_fuel_litres"]=round(result["distance_km"]/model["effective_km_per_l"],3) if model["effective_km_per_l"] else None
        result["estimated_fuel_cost_inr"]=round(result["distance_km"]*model["estimated_cost_per_km_inr"],2) if model["estimated_cost_per_km_inr"] is not None else None
        result["estimate_basis"]={"efficiency_source":model["efficiency_source"],"fuel_price_source":model["fuel_price_source"]}
        return 200,result,"application/json"
    if path == "/api/summary":
        return 200,get_summary(db),"application/json"
    if path == "/api/trips":
        search=(qs.get("q",[""])[0] or "").strip().lower()
        month=(qs.get("month",[""])[0] or "").strip()
        sql="SELECT t.*,v.name AS vehicle_name,v.km_per_l,v.fuel_price_per_litre FROM fuelio_trips t LEFT JOIN vehicles v ON v.id=t.vehicle_id WHERE 1=1"; params=[]
        if search:
            sql += " AND (LOWER(COALESCE(title,'')) LIKE ? OR LOWER(COALESCE(start_name,'')) LIKE ? OR LOWER(COALESCE(end_name,'')) LIKE ? OR start_date LIKE ?)"
            params += [f"%{search}%"]*3+[f"%{search}%"]
        if month:
            sql += " AND substr(start_date,1,7)=?";params.append(month)
        sql += " ORDER BY start_date DESC LIMIT 1000"
        trips=fetch_all(db,sql,params)
        for t in trips:
            t["distance_km"]=round((t.get("distance_m") or 0)/1000,3)
            t["duration_min"]=round((t.get("duration_s") or 0)/60,1)
            t["avg_speed_kmh"]=round((t.get("avg_speed_mps") or 0)*3.6,1)
            t["top_speed_kmh"]=round((t.get("top_speed_mps") or 0)*3.6,1)
            t["trip_cost_inr"]=round(t.get("trip_cost_inr") or 0,2)
            vehicle_row=db.execute("SELECT * FROM vehicles WHERE id=?",(t.get("vehicle_id"),)).fetchone() if t.get("vehicle_id") else None
            model=vehicle_cost_model(db,vehicle_row) if vehicle_row else {}
            t["estimated_fuel_cost_inr"] = round(t["distance_km"]*model["estimated_cost_per_km_inr"],2) if model.get("estimated_cost_per_km_inr") is not None and t["distance_km"] > 0 else None
            t["estimated_cost_basis"]={"efficiency_source":model.get("efficiency_source"),"fuel_price_source":model.get("fuel_price_source")}
            t.pop("raw_json",None);t.pop("last_import_sha",None)
        return 200,trips,"application/json"
    if path == "/api/fuel":
        fuel=fetch_all(db,"SELECT f.*,v.name AS vehicle_name,v.fuel_type FROM fuel_entries f LEFT JOIN vehicles v ON v.id=f.vehicle_id ORDER BY f.date_local DESC")
        for f in fuel:
            f["entry_source"] = "Black Box" if f.get("source_type") == "manual" or str(f.get("guid","")).startswith("manual:") else "Historical import"
            f.pop("raw_json",None);f.pop("last_import_sha",None)
        return 200,fuel,"application/json"
    if path == "/api/costs":
        costs=fetch_all(db,"SELECT c.*,v.name AS vehicle_name FROM cost_entries c LEFT JOIN vehicles v ON v.id=c.vehicle_id ORDER BY c.date_local DESC")
        for f in costs:
            f["entry_source"] = "Black Box" if f.get("source_type") == "manual" or str(f.get("guid","")).startswith("manual:") else "Historical import"
            f.pop("raw_json",None);f.pop("last_import_sha",None)
        return 200,costs,"application/json"
    if path == "/api/tracks":
        tracks=fetch_all(db,"SELECT t.*,v.name AS vehicle_name FROM gpx_tracks t LEFT JOIN vehicles v ON v.id=t.vehicle_id ORDER BY t.start_utc DESC")
        for t in tracks:
            t["source_counts"]=json.loads(t.pop("source_counts_json") or "{}")
        return 200,tracks,"application/json"
    if path.startswith("/api/tracks/"):
        try: track_id=int(path.rsplit("/",1)[1])
        except ValueError: return 400,{"error":"Invalid track id"},"application/json"
        track=db.execute("SELECT t.*,v.name AS vehicle_name FROM gpx_tracks t LEFT JOIN vehicles v ON v.id=t.vehicle_id WHERE t.id=?",(track_id,)).fetchone()
        if not track:return 404,{"error":"Track not found"},"application/json"
        points=fetch_all(db,"SELECT point_index,ts_utc,latitude,longitude,elevation_m,speed_mps,source,satellites,hdop,vdop,pdop FROM gpx_points WHERE track_id=? ORDER BY point_index",(track_id,))
        return 200,{"track":dict(track),"points":points},"application/json"
    if path == "/api/detected-trips":
        return 200,get_detected_trips(db),"application/json"
    if path == "/api/quality":
        return 200,{"issues":get_quality(db),"imports":get_summary(db)["imports"]},"application/json"
    if path == "/api/export/database":
        return 200, export_database_bytes(db),"application/vnd.sqlite3"
    return 404,{"error":"Not found"},"application/json"


def export_database_bytes(db):
    target=sqlite3.connect(":memory:")
    db.backup(target)
    out=io.BytesIO()
    target.serialize if hasattr(target,"serialize") else None
    # Use SQLite backup into a temporary file to support SQLite versions without Connection.serialize().
    tmp=None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite3",delete=False) as f:
            tmp=Path(f.name)
        disk=sqlite3.connect(tmp)
        db.backup(disk)
        disk.close()
        return tmp.read_bytes()
    finally:
        target.close()
        if tmp and tmp.exists():
            try: tmp.unlink()
            except OSError: pass


class Handler(BaseHTTPRequestHandler):
    server_version = "KnowhereBlackBox/0.1"

    def log_message(self, fmt, *args):
        # Do not log coordinates, query strings, or credentials.
        msg = fmt % args
        if "/api/" in msg:
            msg = re.sub(r"/api/[^ ]*", "/api/[redacted]", msg)
        super().log_message("%s", msg)

    def _authorized(self) -> bool:
        if not PASSWORD or PASSWORD in ("CHANGE_ME", "changeme", "password"):
            self.send_error(503, "BLACKBOX_PASSWORD must be set in the .env file")
            return False
        got = self.headers.get("Authorization", "")
        expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        if hmac.compare_digest(got, expected):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Knowhere Black Box", charset="UTF-8"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return False

    def _send_bytes(self, status, body: bytes, content_type="application/octet-stream", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data: https://tile.openstreetmap.org https://basemaps.cartocdn.com; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'")
        if headers:
            for k,v in headers.items(): self.send_header(k,v)
        self.end_headers()
        try: self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError): pass

    def _json(self, status, obj, headers=None):
        body=json.dumps(obj,ensure_ascii=False,default=str).encode("utf-8")
        self._send_bytes(status,body,"application/json; charset=utf-8",headers)

    def do_GET(self):
        parsed=urlparse(self.path)
        if parsed.path == "/api/health":
            return self._json(200,{"ok":True,"service":"knowhere-blackbox"})
        if parsed.path == "/api/location":
            try:
                with DB_LOCK, db_session() as db:
                    result=ingest_location(db,parse_qs(parsed.query, keep_blank_values=True))
                return self._json(200,result)
            except PermissionError as exc:
                return self._json(403,{"error":str(exc)})
            except (ValueError, sqlite3.Error) as exc:
                return self._json(400,{"error":str(exc)[:200]})
        if not self._authorized(): return
        if parsed.path=="/":
            p=APP_DIR/"index.html"
            return self._send_bytes(200,p.read_bytes(),"text/html; charset=utf-8")
        static={"/app.js":("app.js","text/javascript; charset=utf-8"),"/styles.css":("styles.css","text/css; charset=utf-8"),"/favicon.svg":("favicon.svg","image/svg+xml")}
        if parsed.path in static:
            filename,ctype=static[parsed.path]
            p=APP_DIR/filename
            if not p.exists(): return self._send_bytes(404,b"Not found","text/plain")
            return self._send_bytes(200,p.read_bytes(),ctype)
        if parsed.path=="/api/export/database":
            with DB_LOCK, db_session() as db: status,payload,ctype=api_payload(db,parsed)
            return self._send_bytes(status,payload,ctype,{"Content-Disposition":"attachment; filename=knowhere-blackbox-backup.sqlite3"})
        with DB_LOCK, db_session() as db:
            status,payload,ctype=api_payload(db,parsed)
        if ctype=="application/json": return self._json(status,payload)
        return self._send_bytes(status,payload,ctype)

    def do_POST(self):
        if not self._authorized(): return
        parsed=urlparse(self.path)
        if parsed.path == "/api/vehicles":
            try:
                length=int(self.headers.get("Content-Length","0"))
                if length <= 0 or length > 16384:
                    return self._json(413,{"error":"Vehicle payload is empty or too large."})
                payload=json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload,dict): raise ValueError("Expected a JSON object.")
                with DB_LOCK, db_session() as db:
                    vehicle=save_vehicle(db,payload)
                return self._json(200,{"vehicle":vehicle})
            except (ValueError,json.JSONDecodeError) as exc:
                return self._json(400,{"error":str(exc)[:250]})
            except sqlite3.Error:
                return self._json(500,{"error":"Vehicle profile could not be saved."})
        if parsed.path in ("/api/fuel", "/api/costs"):
            try:
                length=int(self.headers.get("Content-Length","0"))
            except ValueError:
                return self._json(400,{"error":"Invalid Content-Length"})
            if length <= 0 or length > 16384:
                return self._json(413,{"error":"Entry payload is empty or too large."})
            try:
                payload=json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Expected a JSON object.")
                with DB_LOCK, db_session() as db:
                    if parsed.path == "/api/fuel":
                        entry=create_manual_fuel(db,payload)
                    else:
                        entry=create_manual_cost(db,payload)
                    summary=get_summary(db)
                return self._json(201,{"entry":entry,"summary":summary})
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json(400,{"error":str(exc)[:250]})
            except sqlite3.Error:
                return self._json(500,{"error":"Database write failed. No entry confirmation was returned."})
        if parsed.path!="/api/import":
            return self._json(404,{"error":"Not found"})
        try:
            length=int(self.headers.get("Content-Length","0"))
        except ValueError:
            return self._json(400,{"error":"Invalid Content-Length"})
        if length<=0 or length>MAX_UPLOAD_BYTES*4:
            return self._json(413,{"error":"Upload batch is empty or larger than 320 MB."})
        body=self.rfile.read(length)
        content_type=self.headers.get("Content-Type", "")
        try:
            envelope=(f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode()+body
            message=BytesParser(policy=email_policy).parsebytes(envelope)
            files=[]
            import_vehicle_id=None
            for part in message.iter_parts():
                name=part.get_filename()
                if name:
                    data=part.get_payload(decode=True) or b""
                    files.append((Path(name).name,data))
                elif part.get_param("name", header="content-disposition") == "vehicle_id":
                    raw=(part.get_payload(decode=True) or b"").decode("utf-8",errors="replace").strip()
                    import_vehicle_id=int_num(raw)
            if import_vehicle_id is not None:
                with DB_LOCK, db_session() as db:
                    if not db.execute("SELECT 1 FROM vehicles WHERE id=?", (import_vehicle_id,)).fetchone():
                        return self._json(400,{"error":"Selected vehicle profile does not exist."})
            if not files:
                return self._json(400,{"error":"No files found in upload."})
            results=[]
            for filename,data in files:
                try:
                    with DB_LOCK, db_session() as db:
                        results.append(import_one(db,filename,data,import_vehicle_id))
                except Exception as exc:
                    results.append({"filename":filename,"status":"error","error":str(exc)[:350],"records":0})
            with DB_LOCK, db_session() as db:
                summary=get_summary(db)
            return self._json(200,{"results":results,"summary":summary})
        except Exception as exc:
            return self._json(400,{"error":f"Could not process upload: {str(exc)[:300]}"})


def self_test():
    import tempfile
    global DB_PATH
    old=DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        DB_PATH=Path(tmp)/"test.sqlite3"
        init_db()
        base=Path(os.environ.get("BLACKBOX_IMPORT_DIR", str(APP_DIR.parent/"private-imports")))
        results=[]
        with db_session() as db:
            for p in sorted(base.glob("*")):
                if p.suffix.lower() in (".csv",".gpx"):
                    results.append(import_one(db,p.name,p.read_bytes()))
            s=get_summary(db)
            detected=get_detected_trips(db)
            quality=get_quality(db)
        assert s["trips"]["count"]==311, s["trips"]
        assert s["fuel"]["count"]==3, s["fuel"]
        assert s["costs"]["count"]==1, s["costs"]
        assert s["gps"]["files"]==4, s["gps"]
        assert s["gps"]["points"]==1195, s["gps"]
        assert abs(s["trips"]["distance_km"]-5990.404)<0.01, s["trips"]
        print(json.dumps({"self_test":"PASS","summary":s,"candidate_trips":len(detected),"quality_issue_count":len(quality),"imports":results},indent=2,ensure_ascii=False))
    DB_PATH=old


def main():
    init_db()
    seed_imports()
    if len(sys.argv)>1 and sys.argv[1]=="--self-test":
        self_test(); return
    if PASSWORD in ("", "CHANGE_ME", "changeme", "password"):
        raise SystemExit("Set BLACKBOX_PASSWORD in your .env file before starting Knowhere Black Box.")
    server=ThreadingHTTPServer(("0.0.0.0",PORT),Handler)
    print(f"Knowhere Black Box listening on 0.0.0.0:{PORT}; database={DB_PATH}; password auth enabled",flush=True)
    server.serve_forever()

if __name__=="__main__": main()
