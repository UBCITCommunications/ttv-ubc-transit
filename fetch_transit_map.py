#!/usr/bin/env python3
"""
Fetches TransLink GTFS Realtime vehicle positions and service alerts.
Filters to our UBC-serving routes and writes transit-live.json.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
from google.transit import gtfs_realtime_pb2


API_KEY = os.environ.get("TRANSLINK_API_KEY")
if not API_KEY:
    print("ERROR: TRANSLINK_API_KEY not set", file=sys.stderr)
    sys.exit(1)

POSITIONS_URL = f"https://gtfsapi.translink.ca/v3/gtfsposition?apikey={API_KEY}"
ALERTS_URL    = f"https://gtfsapi.translink.ca/v3/gtfsalerts?apikey={API_KEY}"

# Load static map data we prepared (route IDs → short names)
with open("transit-map.json", encoding="utf-8") as f:
    STATIC = json.load(f)

ROUTE_ID_TO_SHORT = STATIC["route_id_to_short"]
OUR_ROUTE_IDS = set(ROUTE_ID_TO_SHORT.keys())


def fetch_pb(url):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(r.content)
    return feed


def get_vehicles():
    feed = fetch_pb(POSITIONS_URL)
    vehicles = []
    seen_routes = set()

    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        rid = v.trip.route_id if v.HasField("trip") else ""
        seen_routes.add(rid)
        if rid not in OUR_ROUTE_IDS:
            continue
        if not v.HasField("position"):
            continue
        vehicles.append({
            "r":   ROUTE_ID_TO_SHORT[rid],
            "lat": round(v.position.latitude, 5),
            "lon": round(v.position.longitude, 5),
            "b":   v.position.bearing if v.position.HasField("bearing") else None,
        })

    print(f"  vehicles: {len(vehicles)} on our routes (of {len(seen_routes)} unique routes in feed)")
    return vehicles


def get_alerts():
    try:
        feed = fetch_pb(ALERTS_URL)
    except Exception as e:
        print(f"  alerts fetch failed (non-fatal): {e}")
        return []

    alerts = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        a = entity.alert

        # Which of our routes does this alert affect?
        affected = set()
        for ie in a.informed_entity:
            if ie.route_id and ie.route_id in OUR_ROUTE_IDS:
                affected.add(ROUTE_ID_TO_SHORT[ie.route_id])
        if not affected:
            continue

        # Pick the best human-readable text fields
        header = ""
        description = ""
        for t in a.header_text.translation:
            if t.language in ("", "en"):
                header = t.text
                break
        for t in a.description_text.translation:
            if t.language in ("", "en"):
                description = t.text
                break

        # Active period — drop if it has ended
        now = int(time.time())
        active = True
        if a.active_period:
            active = False
            for p in a.active_period:
                start = p.start if p.HasField("start") else 0
                end   = p.end   if p.HasField("end")   else 9_999_999_999
                if start <= now <= end:
                    active = True
                    break
        if not active:
            continue

        alerts.append({
            "routes":  sorted(affected),
            "header":  header.strip(),
            "desc":    description.strip()[:400],
            "effect":  a.Effect.Name(a.effect) if a.effect else "",
        })

    print(f"  alerts: {len(alerts)} affecting our routes")
    return alerts


def main():
    print("Fetching vehicle positions…")
    vehicles = get_vehicles()
    print("Fetching service alerts…")
    alerts = get_alerts()

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vehicles": vehicles,
        "alerts": alerts,
    }
    with open("transit-live.json", "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))

    print(f"Wrote transit-live.json: {len(vehicles)} vehicles, {len(alerts)} alerts.")


if __name__ == "__main__":
    main()
