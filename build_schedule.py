#!/usr/bin/env python3
"""
Builds schedule.json from the GTFS static files.

For each of our 7 UBC Exchange bays, finds every scheduled departure
and groups them by day-of-week (weekday / saturday / sunday).

Run this whenever TransLink ships a new GTFS zip.
Expects the GTFS files to be in ./gtfs/
"""
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict

GTFS_DIR = "gtfs"

# Our 7 UBC Exchange bays (stop_codes the public sees)
OUR_STOP_CODES = {"60158","60159","60160","60162","60163","60164","61935"}


def load_stops():
    """stop_code -> {id, name, bay}"""
    stops_by_code = {}
    with open(f"{GTFS_DIR}/stops.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["stop_code"] in OUR_STOP_CODES:
                name = row["stop_name"]
                # "UBC Exchange @ Bay 7" -> "7"
                bay = name.replace("UBC Exchange @ Bay ", "").strip()
                stops_by_code[row["stop_code"]] = {
                    "id":   row["stop_id"],
                    "name": name,
                    "bay":  bay,
                    "code": row["stop_code"],
                }
    return stops_by_code


def load_routes():
    """route_id -> short_name"""
    routes = {}
    with open(f"{GTFS_DIR}/routes.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            short = row["route_short_name"].lstrip("0") or row["route_short_name"]
            routes[row["route_id"]] = short
    return routes


def load_trips():
    """trip_id -> (route_id, service_id, headsign)"""
    trips = {}
    with open(f"{GTFS_DIR}/trips.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            trips[row["trip_id"]] = (
                row["route_id"],
                row["service_id"],
                row["trip_headsign"],
            )
    return trips


def load_calendar():
    """service_id -> set of weekdays it runs ('mon','tue',...)"""
    DAYS = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    service_days = {}
    try:
        with open(f"{GTFS_DIR}/calendar.txt", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                days = {d for d in DAYS if row.get(d) == "1"}
                service_days[row["service_id"]] = days
    except FileNotFoundError:
        pass
    return service_days


def parse_time_to_minutes(t):
    """'14:23:00' -> 14*60+23. Handles GTFS 24h+ overflow ('25:30:00' = next day 1:30 AM)."""
    h, m, s = t.strip().split(":")
    return int(h) * 60 + int(m)


def clean_headsign(h):
    """'99 Broadway B-Line/To Boundary Loop' -> 'Boundary Loop'"""
    if "/To " in h:
        return h.split("/To ", 1)[1].strip()
    return h.strip()


def main():
    print("Loading lookup tables...")
    stops_by_code   = load_stops()
    stops_by_id     = {s["id"]: s for s in stops_by_code.values()}
    our_stop_ids    = set(stops_by_id.keys())
    routes          = load_routes()
    trips           = load_trips()
    service_days    = load_calendar()
    print(f"  stops: {len(stops_by_code)}, routes: {len(routes)}, trips: {len(trips):,}, services: {len(service_days)}")

    # Walk stop_times.txt — big but we only care about our 7 stops
    print("Scanning stop_times.txt for departures from UBC Exchange...")
    matches = []
    with open(f"{GTFS_DIR}/stop_times.txt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["stop_id"] not in our_stop_ids:
                continue
            trip_id = row["trip_id"]
            if trip_id not in trips:
                continue
            route_id, service_id, headsign = trips[trip_id]
            matches.append({
                "stop_id":      row["stop_id"],
                "service_id":   service_id,
                "minute":       parse_time_to_minutes(row["departure_time"]),
                "route":        routes.get(route_id, "?"),
                "headsign":     clean_headsign(headsign),
            })
    print(f"  found {len(matches):,} scheduled departures across all service days")

    # Group by day-of-week
    # We collapse to 3 buckets (weekday / saturday / sunday) since TransLink
    # typically has the same weekday schedule Mon-Fri.
    print("Bucketing into weekday/saturday/sunday...")
    by_day = {"weekday": [], "saturday": [], "sunday": []}

    for m in matches:
        days = service_days.get(m["service_id"], set())
        if any(d in days for d in ["monday","tuesday","wednesday","thursday","friday"]):
            by_day["weekday"].append(m)
        if "saturday" in days:
            by_day["saturday"].append(m)
        if "sunday" in days:
            by_day["sunday"].append(m)

    # De-dupe: same trip can have multiple service_ids that all map to weekday;
    # we'd see it twice. Key = (stop_id, route, headsign, minute).
    # Also drop service_id from output — page doesn't need it.
    def slim(records):
        seen = set()
        out = []
        for r in records:
            key = (r["stop_id"], r["route"], r["headsign"], r["minute"])
            if key in seen:
                continue
            seen.add(key)
            stop = stops_by_id[r["stop_id"]]
            out.append({
                "r": r["route"],          # route short name
                "h": r["headsign"],       # destination
                "b": stop["bay"],         # bay number
                "c": stop["code"],        # stop code
                "m": r["minute"],         # minute-of-day (0-1439, or higher for overnight)
            })
        # Sort by minute, then route
        out.sort(key=lambda x: (x["m"], x["r"]))
        return out

    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "stops": stops_by_code,
        "schedule": {day: slim(recs) for day, recs in by_day.items()},
    }

    for day, recs in output["schedule"].items():
        print(f"  {day}: {len(recs):,} departures")

    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))

    size_kb = os.path.getsize("schedule.json") / 1024
    print(f"\nWrote schedule.json ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
