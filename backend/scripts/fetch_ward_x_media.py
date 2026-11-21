"""
Download the complainant's own photos/videos from the X post behind each of
one ward's complaints (GCC files nearly every complaint from an X post).

Uses X's public embed endpoint (the one embedded tweets on other sites use,
and the one Publyq's x_authors stage already uses): no login. Photos come at
full size (?name=orig); videos as the highest-bitrate MP4.

Writes, under --out:
  images/<complaint_id>__complainant__x<NN>.<jpg|mp4|...>
  x_media_manifest.csv   one row per file (and one per post with no media / gone)

Only the post's own media is taken. When the complaint post quotes or replies
to another post, that post's media is not the complainant's and is skipped
(counted in the manifest as quoted_media / parent_media).

Resumable: posts already done are skipped. Rerun to retry failures.

  python backend/scripts/fetch_ward_x_media.py --ward 173
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUBLYQ = HERE.parents[2] / "Publyq"
sys.path.insert(0, str(PUBLYQ))

import pandas as pd  # noqa: E402
from pipeline.enrich.x_authors import ENDPOINT, tweet_id  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
UA = {"User-Agent": "Mozilla/5.0"}   # as pipeline.enrich.x_authors sends
COLS = ["complaint_id", "tweet_id", "file", "media_type", "index", "source_url", "bytes",
        "tweet_created_at", "tweet_text", "quoted_media", "parent_media", "status", "fetched_at"]


def get(url: str, timeout: float = 60) -> tuple[bytes, str]:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read(), (r.headers.get("content-type") or "").split(";")[0].lower()


def media_urls(m: dict) -> tuple[str, str]:
    """(download url, extension) for one mediaDetails entry."""
    if m.get("type") == "photo":
        base = m["media_url_https"]
        ext = base.rsplit(".", 1)[-1]
        return f"{base.rsplit('.', 1)[0]}?format={ext}&name=orig", ext
    mp4 = [v for v in (m.get("video_info") or {}).get("variants", []) if v.get("content_type") == "video/mp4"]
    if mp4:
        return max(mp4, key=lambda v: v.get("bitrate") or 0)["url"], "mp4"
    return m.get("media_url_https", ""), "jpg"   # no playable variant: keep the thumbnail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ward", default="173")
    ap.add_argument("--out", default=str(HERE.parents[1] / "data" / "complaints"))
    ap.add_argument("--gap", type=float, default=1.5)
    args = ap.parse_args()

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    c = pd.read_csv(PUBLYQ / "data" / "erp" / "enriched" / "complaints.csv", dtype=str).fillna("")
    w = c[c.ward_id == args.ward].sort_values("complaint_id")
    posts = [(r.complaint_id, tweet_id(r.tweet_url)) for r in w.itertuples()]

    mpath = out / "x_media_manifest.csv"
    rows: list[dict] = []
    if mpath.exists():
        with mpath.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    finished = {r["complaint_id"] for r in rows if r["status"] in ("ok", "no media", "gone", "no post")}
    rows = [r for r in rows if r["complaint_id"] in finished]
    todo = [p for p in posts if p[0] not in finished]
    print(f"ward {args.ward}: {len(posts)} complaints, {len(todo)} posts to look up", flush=True)

    def save():
        with mpath.open("w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, COLS, extrasaction="ignore")
            wr.writeheader()
            wr.writerows(sorted(rows, key=lambda r: (r["complaint_id"], str(r.get("index", "")))))

    errors = 0
    for n, (cid, tid) in enumerate(todo, 1):
        now = datetime.now(IST).isoformat(timespec="seconds")
        base = dict(complaint_id=cid, tweet_id=tid, fetched_at=now)
        if not tid:
            rows.append({**base, "status": "no post"})
            continue
        try:
            body, _ = get(ENDPOINT.format(id=tid), timeout=20)
            d = json.loads(body or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                rows.append({**base, "status": "gone"})
            else:
                rows.append({**base, "status": f"failed: HTTP {e.code}"})
                errors += 1
                time.sleep(60 if e.code == 429 else 10)
            continue
        except Exception as e:  # noqa: BLE001
            rows.append({**base, "status": f"failed: {type(e).__name__}"})
            errors += 1
            time.sleep(10)
            continue
        if not d or d.get("__typename") == "TweetTombstone":
            rows.append({**base, "status": "gone"})
            continue
        info = dict(base, tweet_created_at=d.get("created_at", ""), tweet_text=re.sub(r"\s+", " ", d.get("text", "")),
                    quoted_media=len((d.get("quoted_tweet") or {}).get("mediaDetails") or []),
                    parent_media=len((d.get("parent") or {}).get("mediaDetails") or []))
        media = d.get("mediaDetails") or []
        if not media:
            rows.append({**info, "status": "no media"})
        for i, m in enumerate(media, 1):
            url, ext = media_urls(m)
            name = f"{cid}__complainant__x{i:02d}.{ext}"
            row = {**info, "file": f"images/{name}", "media_type": m.get("type", ""), "index": i, "source_url": url}
            try:
                data, _ = get(url, timeout=180)
                (out / "images" / name).write_bytes(data)
                row.update(bytes=len(data), status="ok")
            except Exception as e:  # noqa: BLE001
                row.update(status=f"failed: {type(e).__name__}")
                errors += 1
            rows.append(row)
            time.sleep(0.5)
        if n % 25 == 0:
            save()
            print(f"{n}/{len(todo)}  files ok {sum(r['status'] == 'ok' for r in rows)}", flush=True)
        time.sleep(args.gap)
    save()
    ok = [r for r in rows if r["status"] == "ok"]
    by = lambda s: len({r["complaint_id"] for r in rows if r["status"] == s})  # noqa: E731
    print(f"done: {len(ok)} files ({sum(r['media_type'] == 'photo' for r in ok)} photos, "
          f"{sum(r['media_type'] != 'photo' for r in ok)} videos/gifs, "
          f"{sum(int(r['bytes']) for r in ok) / 1e6:.0f} MB) from {len({r['complaint_id'] for r in ok})} posts; "
          f"{by('no media')} posts without media, {by('gone')} gone, {by('no post')} with no X post; "
          f"{sum(1 for r in rows if r['status'].startswith('failed'))} failed (rerun to retry)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
