# eightrooms-watcher

Checks SevenRooms restaurants for bookable tables (e.g. after cancellations) and sends a
Telegram message when a wanted slot opens up. Runs on GitHub Actions; standard-library Python only.

## Add a restaurant

Add an entry to `venues` in [watchlist.json](watchlist.json):

```json
{
  "venue": "the-slug",
  "name": "Display name",
  "party_sizes": [2, 4],
  "windows": { "fri": ["19:00-21:00"], "sat": ["00:00-23:59"] }
}
```

- `venue` is the part after `sevenrooms.com/explore/` in the restaurant's booking link.
- `party_sizes`: the first is preferred; later sizes are only reported for times the first has nothing.
- `windows`: wanted start times per weekday (`mon`..`sun`); days left out are ignored.
- Optional `utc_offset` for venues outside Singapore (default 8).

Check it with `python scanner.py --all` (lists every bookable slot, ignoring windows).

## Setup

Repo secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (locally: a `.env` file, see `.env.example`).
