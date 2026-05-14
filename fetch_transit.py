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
    rejected_matches = []  # matches that failed display filters

    # Debug: track what stop IDs we're seeing in the wild
    seen_stop_ids = {}
    total_stop_updates = 0

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id = tu.trip.trip_id
        trip = TRIPS.get(trip_id)
        if not trip:
            continue  # trip not in our static (e.g. added trip we don't recognize)

        for stu in tu.stop_time_update:
            total_stop_updates += 1
            sid = stu.stop_id
            seen_stop_ids[sid] = seen_stop_ids.get(sid, 0) + 1

            if sid not in OUR_STOP_IDS:
                continue

            # === We have a match — capture EVERYTHING about it for debug ===
            has_dep = stu.HasField("departure") and stu.departure.time
            has_arr = stu.HasField("arrival") and stu.arrival.time
            eta = (stu.departure.time if has_dep else
                   stu.arrival.time   if has_arr else None)

            debug_info = {
                "stop_id": sid,
                "trip_id": trip_id,
                "route":   ROUTES.get(trip["r"], {}).get("short", "?"),
                "has_departure": bool(has_dep),
                "has_arrival": bool(has_arr),
                "eta_unix": eta,
                "min_away": round((eta - now) / 60, 1) if eta else None,
            }

            # Prefer departure time, fall back to arrival
            if stu.HasField("departure") and stu.departure.time:
                eta = stu.departure.time
            elif stu.HasField("arrival") and stu.arrival.time:
                eta = stu.arrival.time
            else:
                debug_info["rejected"] = "no eta"
                rejected_matches.append(debug_info)
                continue

            minutes_away = (eta - now) / 60
            if minutes_away < -1:
                debug_info["rejected"] = f"already left ({minutes_away:.1f} min ago)"
                rejected_matches.append(debug_info)
                continue  # already left (small grace for clock skew)
            if minutes_away > 60:
                debug_info["rejected"] = f"too far out ({minutes_away:.0f} min)"
                rejected_matches.append(debug_info)
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

    # === DEBUG ===
    print(f"\n--- DEBUG ---")
    print(f"Total stop_time_updates seen: {total_stop_updates}")
    print(f"Unique stop IDs seen in feed: {len(seen_stop_ids)}")
    print(f"Our stop IDs (looking for these): {sorted(OUR_STOP_IDS)}")

    # Check each of our stop IDs individually
    print(f"\nOur stops in the feed RIGHT NOW:")
    for sid in sorted(OUR_STOP_IDS):
        stop_name = STOPS[sid]["name"]
        count = seen_stop_ids.get(sid, 0)
        print(f"  {sid} ({stop_name}): {count} updates")

    # Are any of our codes (the public-facing numbers) showing up instead?
    our_codes = {STOPS[sid]["code"] for sid in OUR_STOP_IDS}
    matches_by_code = [c for c in our_codes if c in seen_stop_ids]
    print(f"\nOur stop CODES (60xxx) that appear in realtime feed: {matches_by_code}")

    # Show the matches that were rejected, with reasons
    if rejected_matches:
        print(f"\nMatches we found at our stops but didn't display ({len(rejected_matches)}):")
        for m in rejected_matches[:20]:
            print(f"  route {m['route']} at stop {m['stop_id']}: {m.get('rejected','?')} "
                  f"(has_dep={m['has_departure']}, has_arr={m['has_arrival']}, min={m['min_away']})")
    else:
        print(f"\nNo matches at our stops at all this run.")


if __name__ == "__main__":
    main()
