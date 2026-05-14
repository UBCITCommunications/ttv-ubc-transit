#!/usr/bin/env python3
"""
Fetches TransLink GTFS Realtime Trip Updates, filters to our stops,
and writes a tiny transit.json the signage page can read.
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

FEED_URL = f"https://gtfsapi.translink.ca/v3/gtfsrealtime?apikey={API_KEY}"

# Load the static lookups we prepared once from the GTFS zip
with open("gtfs-lookup.json", encoding="utf-8") as f:
    LOOKUP = json.load(f)

OUR_STOP_IDS = set(LOOKUP["our_stop_ids"])
STOPS  = LOOKUP["stops"]
ROUTES = LOOKUP["routes"]
TRIPS  = LOOKUP["trips"]


def fetch_feed():
    r = requests.get(FEED_URL, timeout=30)
    r.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(r.content)
    return feed


def clean_headsign(h):
    """'99 Broadway B-Line/To Boundary Loop' -> 'Boundary Loop'"""
    if "/To " in h:
        return h.split("/To ", 1)[1].strip()
    return h.strip()


def main():
    feed = fetch_feed()
    now = int(time.time())
    departures = []

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id = tu.trip.trip_id
        trip = TRIPS.get(trip_id)
        if not trip:
            continue  # trip not in our static (e.g. added trip we don't recognize)

        for stu in tu.stop_time_update:
            if stu.stop_id not in OUR_STOP_IDS:
                continue

            # Prefer departure time, fall back to arrival
            if stu.HasField("departure") and stu.departure.time:
                eta = stu.departure.time
            elif stu.HasField("arrival") and stu.arrival.time:
                eta = stu.arrival.time
            else:
                continue

            minutes_away = (eta - now) / 60
            if minutes_away < -1:
                continue  # already left (small grace for clock skew)
            if minutes_away > 60:
                continue  # too far out to be useful

            route = ROUTES.get(trip["r"], {})
            stop  = STOPS.get(stu.stop_id, {})
            departures.append({
                "route":     route.get("short", "?").lstrip("0") or "?",
                "headsign":  clean_headsign(trip["h"]),
                "bay":       stop.get("name", "").replace("UBC Exchange @ Bay ", ""),
                "stop_code": stop.get("code"),
                "eta_unix":  eta,
                "min":       round(minutes_away),
            })

    # Sort by soonest departure
    departures.sort(key=lambda d: d["eta_unix"])

    # Drop "leaving in <5 min" — too stale to trust given our 5-min cache
    visible = [d for d in departures if d["min"] >= 5]

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "departures": visible[:30],   # plenty for the board
        "total_seen": len(departures),
    }

    with open("transit.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print(f"Wrote {len(visible)} departures (of {len(departures)} seen).")


if __name__ == "__main__":
    main()
