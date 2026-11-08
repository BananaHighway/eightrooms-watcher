"""Watch SevenRooms restaurants for bookable tables and Telegram any newly opened ones.

Restaurants, party sizes and wanted times live in watchlist.json (see its "_help").

The SevenRooms booking widget reads a public JSON endpoint (no login). Each time slot is
either type "book" (a real table you can reserve now) or "request" (a request-list
placeholder). Only "book" slots count.

State (state.json) remembers what was open last run, so we only ping on change.

Usage:  python scanner.py            # scan + alert on new slots
        python scanner.py --dry-run  # scan + print, no Telegram, no state change
        python scanner.py --all      # with --dry-run: list every bookable slot, ignoring windows
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

HERE = Path(__file__).parent
WATCHLIST = HERE / "watchlist.json"
STATE_FILE = HERE / "state.json"
API = "https://www.sevenrooms.com/api-yoa/availability/widget/range"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MAX_DAYS_PER_CALL = 3  # the API rejects num_days > 3


def load_env(path=HERE / ".env"):
    """Minimal .env loader; real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def book_link(venue):
    return f"https://www.sevenrooms.com/explore/{venue}/reservations/create/search"


def fetch(venue: str, start: date, num_days: int, party: int):
    """Return {"YYYY-MM-DD": [shift, ...]} for one venue and party size."""
    q = urllib.parse.urlencode({
        "venue": venue, "time_slot": "18:00", "party_size": party,
        "halo_size_interval": 100,  # +-100 x 15 min around time_slot = the whole day
        "start_date": f"{start:%m-%d-%Y}", "num_days": num_days, "channel": "SEVENROOMS_WIDGET",
    })
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["data"]["availability"]


def wanted(start: datetime, windows: dict) -> bool:
    hhmm = f"{start:%H:%M}"
    for rng in windows.get(WEEKDAYS[start.weekday()], []):
        lo, hi = rng.split("-")
        if lo <= hhmm <= hi:
            return True
    return False


def scan_venue(cfg, days_ahead, ignore_windows=False):
    """Return sorted [(start, party)]: one entry per time, with the most preferred party size open."""
    tz = timezone(timedelta(hours=cfg.get("utc_offset", 8)))
    now = datetime.now(tz)
    sizes = cfg["party_sizes"]
    open_for = {}  # start -> set of party sizes with a bookable table
    for party in sizes:
        for offset in range(0, days_ahead + 1, MAX_DAYS_PER_CALL):
            days = fetch(cfg["venue"], now.date() + timedelta(days=offset), MAX_DAYS_PER_CALL, party)
            for shifts in days.values():
                for shift in shifts:
                    for t in shift.get("times", []):
                        if t.get("type") != "book":
                            continue
                        start = datetime.strptime(t["time_iso"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
                        if start > now and (ignore_windows or wanted(start, cfg["windows"])):
                            open_for.setdefault(start, set()).add(party)
            time.sleep(0.5)
    return sorted((s, min(ps, key=sizes.index)) for s, ps in open_for.items())


def send_telegram(text, button=None):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram not configured; skipping alert")
        return False
    payload = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    if button:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]}
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status == 200


def key(venue, start, party):
    return f"{venue}|{start:%Y-%m-%d %H:%M}|{party}"


def label(party, sizes):
    return f"for {party}" if party == sizes[0] else f"for {party} only"


def main():
    load_env()
    if "--test-alert" in sys.argv:
        print("sent" if send_telegram("🍽 Table watcher: test alert ✅") else "failed")
        return

    watch = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    dry = "--dry-run" in sys.argv or "--all" in sys.argv
    prev = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else set()
    current, failed = set(), []

    for cfg in watch["venues"]:
        venue, name, sizes = cfg["venue"], cfg.get("name", cfg["venue"]), cfg["party_sizes"]
        try:
            found = scan_venue(cfg, watch.get("days_ahead", 35), ignore_windows="--all" in sys.argv)
        except Exception as e:  # one broken venue shouldn't stop the others
            print(f"{name}: scan failed: {e}")
            failed.append(venue)
            current |= {k for k in prev if k.startswith(venue + "|")}  # keep its state as-is
            continue
        print(f"{name}: {len(found)} bookable target slot(s)")
        for start, party in found:
            print(f"  {start:%a %d %b %H:%M}  {label(party, sizes)}")
        keys = {key(venue, s, p) for s, p in found}
        current |= keys
        new = [(s, p) for s, p in found if key(venue, s, p) not in prev]
        if new and not dry:
            lines = [f"• {s:%a %d %b} {s:%H:%M}  {label(p, sizes)}" for s, p in new]
            send_telegram(f"🍽 {name}: table(s) opened\n" + "\n".join(lines),
                          button=("Book on SevenRooms", book_link(venue)))
            print(f"  alerted {len(new)} new slot(s)")

    if not dry:
        # Drop past/taken slots so a slot that frees up again re-alerts.
        STATE_FILE.write_text(json.dumps(sorted(current), indent=1))
    if failed:
        sys.exit(f"Scan failed for: {', '.join(failed)}")  # mark the GitHub run red


if __name__ == "__main__":
    main()
