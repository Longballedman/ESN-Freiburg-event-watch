#!/usr/bin/env python3
"""
Notifies you when a new event shows up in the ESN Freiburg ticket shop
(https://freiburg.esn-germany.de/events -> tickets.esn-germany.de/esnfreiburg).

Reads the shop's calendar data directly, month by month (current month + 12 ahead).
No browser needed.

Setup:    pip install requests            (optional for desktop popups: pip install plyer)
Run:      python esn_event_watcher.py              # checks every 10 min, forever
          python esn_event_watcher.py --once       # one check (cron / Task Scheduler / GitHub Actions)
          python esn_event_watcher.py --debug      # list all upcoming events, no notifications

Phone notifications via ntfy.sh: set the NTFY_TOPIC environment variable to your topic name.
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

BASE_URL = "https://tickets.esn-germany.de/esnfreiburg/widget/product_list"
MONTHS_AHEAD = 12
CHECK_INTERVAL_SECONDS = 600
STATE_FILE = Path(__file__).with_name("seen_events.json")
STATE_VERSION = 2
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")


def month_starts(n_ahead):
    today = date.today()
    y, m = today.year, today.month
    for _ in range(n_ahead + 1):
        yield date(y, m, 1)
        m += 1
        if m == 13:
            y, m = y + 1, 1


def parse_month(data, today):
    """Turn one month of calendar JSON into {event_key: info}."""
    events = {}
    for week in data.get("weeks", []):
        for day in week:
            if not day or day["date"] < today.isoformat():
                continue
            for ev in day.get("events", []):
                url = ev["event_url"]
                # Multi-date series share one URL, so add the date to tell them apart
                key = f"{url}|{day['date']}" if ev.get("subevent") else url
                events[key] = {
                    "name": ev["name"],
                    "when": ev.get("date_range", day["date"]),
                    "location": ev.get("location", ""),
                    "status": (ev.get("availability") or {}).get("text", ""),
                    "url": url,
                }
    return events


def fetch_all_events():
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (esn-event-watcher)"
    today = date.today()
    all_events, failures = {}, 0
    for month in month_starts(MONTHS_AHEAD):
        try:
            r = session.get(
                BASE_URL,
                params={"lang": "en", "style": "calendar", "date": month.isoformat()},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("date", "")[:7] != month.isoformat()[:7]:
                raise RuntimeError(
                    f"asked for {month:%Y-%m} but server returned {data.get('date')}"
                )
            all_events.update(parse_month(data, today))
        except Exception as e:
            failures += 1
            print(f"  {month:%Y-%m}: failed ({e})", file=sys.stderr)
    return all_events, failures


def notify(ev):
    title = "New ESN Freiburg event"
    message = "\n".join(x for x in (ev["name"], ev["when"], ev["location"], ev["status"]) if x)
    print(f"[NOTIFY] {ev['name']} - {ev['when']} - {ev['url']}")

    if NTFY_TOPIC:
        try:
            requests.post(
                "https://ntfy.sh",
                json={"topic": NTFY_TOPIC, "title": title, "message": message, "click": ev["url"]},
                timeout=15,
            ).raise_for_status()
        except Exception as e:
            print(f"  ntfy failed: {e}", file=sys.stderr)
    try:
        from plyer import notification

        notification.notify(title=title, message=message[:250], timeout=15)
    except Exception:
        pass  # plyer not installed or no desktop (e.g. on a server): ignore


def load_seen():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("version") == STATE_VERSION:
            return state["events"]
    except (FileNotFoundError, ValueError):
        pass
    return None


def save_seen(events):
    STATE_FILE.write_text(
        json.dumps({"version": STATE_VERSION, "events": events}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def check(debug=False):
    events, failures = fetch_all_events()

    if debug:
        print(f"Found {len(events)} upcoming events ({failures} month(s) failed):")
        for ev in sorted(events.values(), key=lambda e: e["when"]):
            print(f" - {ev['name']} | {ev['when']} | {ev['location'] or '-'} | {ev['status']}\n   {ev['url']}")
        return

    seen = load_seen()
    if seen is None:
        if failures:
            print("Baseline run had failures; not saving. Will retry next time.")
            return
        save_seen({k: v["name"] for k, v in events.items()})
        print(f"First run: saved {len(events)} existing events as baseline.")
        return

    new = {k: v for k, v in events.items() if k not in seen}
    for ev in new.values():
        notify(ev)
    if new:
        seen.update({k: v["name"] for k, v in new.items()})
        save_seen(seen)
    else:
        print(time.strftime("%H:%M:%S"), f"- no new events ({len(events)} upcoming).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="check once and exit")
    ap.add_argument("--debug", action="store_true", help="list upcoming events only")
    args = ap.parse_args()

    if args.once or args.debug:
        check(debug=args.debug)
        return
    while True:
        try:
            check()
        except Exception as e:
            print(f"Check failed: {e}", file=sys.stderr)
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
