"""Watch Naoh (SevenRooms) for bookable tables and Telegram any newly opened ones.

Targets: Tue-Fri dinners from 19:30, and Saturday lunch or dinner at any time (closed Sun/Mon).
Party of 2; a time that only has a table for 4 is alerted too, labelled "for 4 only".

The SevenRooms booking widget reads a public JSON endpoint (no login). Each time slot is
either type "book" (a real table you can reserve now) or "request" (a request-list
placeholder). Only "book" slots count.

State (state.json) remembers what was open last run, so we only ping on change.

Usage:  python scanner.py            # scan + alert on new slots
        python scanner.py --dry-run  # scan + print, no Telegram, no state change
        python scanner.py --test-alert
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SGT = timezone(timedelta(hours=8))
VENUE = "naohrestaurant"
PARTY_SIZES = (2, 4)  # 2 preferred; 4 alerted only where 2 has nothing
DAYS_AHEAD = 35       # release is ~1 month ahead at 10:00 daily; a few extra days is harmless
WEEKDAY_FROM = "19:30"
API = "https://www.sevenrooms.com/api-yoa/availability/widget/range"
BOOK_LINK = f"https://www.sevenrooms.com/explore/{VENUE}/reservations/create/search"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
STATE_FILE = Path(__file__).with_name("state.json")


def load_env(path=Path(__file__).with_name(".env")):
    """Minimal .env loader; real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def fetch(start: date, num_days: int, party: int):
    """Return {"YYYY-MM-DD": [shift, ...]}. The API allows at most 3 days per call."""
    q = urllib.parse.urlencode({
        "venue": VENUE, "time_slot": "18:00", "party_size": party,
        "halo_size_interval": 100,  # +-100 x 15 min around time_slot = the whole day
        "start_date": f"{start:%m-%d-%Y}", "num_days": num_days, "channel": "SEVENROOMS_WIDGET",
    })
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["data"]["availability"]


def is_target(start: datetime) -> bool:
    wd = start.weekday()  # Mon=0 .. Sat=5
    if wd == 5:
        return True
    return 1 <= wd <= 4 and f"{start:%H:%M}" >= WEEKDAY_FROM


def scan():
    now = datetime.now(SGT)
    open_for = {}  # start datetime -> set of party sizes with a bookable table
    for party in PARTY_SIZES:
        for offset in range(0, DAYS_AHEAD + 1, 3):
            for shifts in fetch(now.date() + timedelta(days=offset), 3, party).values():
                for shift in shifts:
                    for t in shift.get("times", []):
                        if t.get("type") != "book":
                            continue
                        start = datetime.strptime(t["time_iso"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=SGT)
                        if start > now and is_target(start):
                            open_for.setdefault(start, set()).add(party)
            time.sleep(0.5)
    # One entry per time: "2" if a table for 2 exists, else "4" (table for 4 only).
    return sorted((s, min(p)) for s, p in open_for.items())


def send_telegram(text):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram not configured; skipping alert")
        return False
    payload = {"chat_id": chat, "text": text, "disable_web_page_preview": True,
               "reply_markup": {"inline_keyboard": [[{"text": "🍽 Book on SevenRooms", "url": BOOK_LINK}]]}}
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status == 200


def key(start, party):
    return f"{start:%Y-%m-%d %H:%M}|{party}"


def label(party):
    return "for 2" if party == 2 else "for 4 only"


def main():
    load_env()
    if "--test-alert" in sys.argv:
        print("sent" if send_telegram("🍽 Naoh watcher: test alert ✅") else "failed")
        return

    found = scan()
    stamp = datetime.now(SGT).strftime("%a %d %b %H:%M")
    print(f"[{stamp} SGT] {len(found)} bookable target slot(s)")
    for start, party in found:
        print(f"  {start:%a %d %b %H:%M}  {label(party)}")
    if "--dry-run" in sys.argv:
        return

    prev = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else set()
    current = {key(s, p) for s, p in found}
    new = [(s, p) for s, p in found if key(s, p) not in prev]
    if new:
        lines = [f"• {s:%a %d %b} {s:%H:%M}  {label(p)}" for s, p in new]
        send_telegram("🍽 Naoh table(s) opened:\n" + "\n".join(lines))
        print(f"Alerted {len(new)} new slot(s)")
    # Drop past/taken slots so a slot that frees up again re-alerts.
    STATE_FILE.write_text(json.dumps(sorted(current), indent=1))


if __name__ == "__main__":
    main()
