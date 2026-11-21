"""
Download every photo/video GCC's ERP shows for one ward's complaints.

Reads Publyq's enriched data (read-only) and writes, under --out:
  ward<N>_complaints.csv   the ward's enriched complaints
  ward<N>_events.csv       their enriched events (officer / QA updates)
  images/                  one file per image, named so it identifies itself
  images_manifest.csv      one row per image: who uploaded it, when, which update

Who uploaded what (from where the link sits on the ERP page):
  complainant  the "View Photograph/Video Uploaded By Citizen" link in the
               complaint's own section — the citizen's photo with the complaint
  officer      a link on a field officer's update (AE, SI, AEE, ...)
  qa           a link on GCC's quality-assurance desk update (closes complaints)
  Officer/QA links come in two kinds: "before" (openbfImages, the site before
  the work) and "after" (openImages, the work done).

File names:  <complaint_id>__<uploader>[__e<seq>]__<before|after|complaint>.<ext>
  e.g. 2026-100LSP__officer__e01__before.jpg, 2026-100LSP__complainant__complaint.jpg

Polite and resumable: one request at a time, a gap between requests, a pause
after repeated failures, files already downloaded are skipped, and it stops
at --until (default 06:00 IST, the end of the night window).

  python backend/scripts/fetch_ward_images.py --ward 173
"""
from __future__ import annotations

import argparse
import csv
import glob
import gzip
import hashlib
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PUBLYQ = HERE.parents[2] / "Publyq"
sys.path.insert(0, str(PUBLYQ))

import pandas as pd  # noqa: E402
from pipeline.erp.fetch import Fetcher  # noqa: E402  (same session settings as the nightly fetcher)
from pipeline.erp.store import RawStore  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
IMAGE_URL = "https://erp.chennaicorporation.gov.in/pgr/citizen/ViewImageVideoUpload.do?{}uuid={}"
UPLOADER = {"citizen": "complainant", "officer": "officer", "qa": "qa"}
EXT = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/gif": "gif",
       "video/mp4": "mp4", "video/quicktime": "mov", "video/3gpp": "3gp", "application/pdf": "pdf"}
CITIZEN_RE = re.compile(r"Uploaded By Citizen[\s\S]{0,800}?openImages\('([0-9a-f-]+)'\)")
MANIFEST_COLS = ["complaint_id", "file", "uploader", "stage", "event_seq", "event_at", "author",
                 "role", "uuid", "source_url", "content_type", "bytes", "md5", "same_as", "status",
                 "fetched_at"]


def plan(ward: str, out: Path) -> list[dict]:
    data = PUBLYQ / "data" / "erp"
    c = pd.read_csv(data / "enriched" / "complaints.csv", dtype=str).fillna("")
    e = pd.read_csv(data / "enriched" / "events.csv", dtype=str).fillna("")
    wc = c[c.ward_id == ward].sort_values("complaint_id")
    we = e[e.complaint_id.isin(wc.complaint_id)].sort_values(["complaint_id", "seq"], key=lambda s: s if s.name != "seq" else s.astype(int))
    wc.to_csv(out / f"ward{ward}_complaints.csv", index=False)
    we.to_csv(out / f"ward{ward}_events.csv", index=False)
    print(f"ward {ward}: {len(wc)} complaints, {len(we)} events written")

    items, seen = [], set()

    def add(cid, uploader, stage, uuid, seq="", at="", author="", role=""):
        bf = "bfimage=yes&" if stage == "before" else ""
        if (uuid, bf) in seen:
            return
        seen.add((uuid, bf))
        tag = f"__e{int(seq):02d}" if seq else ""
        items.append(dict(complaint_id=cid, stem=f"{cid}__{uploader}{tag}__{stage}", uploader=uploader,
                          stage=stage, event_seq=seq, event_at=at, author=author, role=role, uuid=uuid,
                          source_url=IMAGE_URL.format(bf, uuid)))

    for cid in wc.complaint_id:
        pages = sorted(glob.glob(str(data / "raw" / cid / "*.html.gz")))
        if pages:
            m = CITIZEN_RE.search(gzip.open(pages[-1], "rt", errors="replace").read())
            if m:
                add(cid, "complainant", "complaint", m.group(1))
    for r in we.itertuples():
        who = UPLOADER.get(r.author_type, r.author_type or "unknown")
        for stage, col in (("before", r.before_media_uuids), ("after", r.media_uuids)):
            for u in filter(None, col.split("|")):
                add(r.complaint_id, who, stage, u, r.seq, r.at, r.author, r.role)
    return items


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["source_url"]: r for r in csv.DictReader(f)}


def save_manifest(path: Path, rows: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, MANIFEST_COLS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: (r["complaint_id"], r["file"] or r["source_url"])):
            w.writerow(r)
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ward", default="173")
    ap.add_argument("--out", default=str(HERE.parents[1] / "data" / "complaints"))
    ap.add_argument("--gap", type=float, default=2.0, help="seconds between requests")
    ap.add_argument("--until", default="06:00", help="stop at this IST time (HH:MM)")
    args = ap.parse_args()

    out = Path(args.out)
    img_dir = out / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(IST)
    h, m = map(int, args.until.split(":"))
    stop = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if stop <= now:
        stop += timedelta(days=1)

    items = plan(args.ward, out)
    mpath = out / "images_manifest.csv"
    done = load_manifest(mpath)
    todo = [i for i in items if done.get(i["source_url"], {}).get("status") != "ok"]
    print(f"{len(items)} images planned, {len(items) - len(todo)} already downloaded, "
          f"{len(todo)} to fetch; stopping at {stop:%H:%M} IST")

    s = Fetcher(RawStore(PUBLYQ / "data" / "erp"))._session()
    fails = 0
    by_md5 = {r["md5"]: r["file"] for r in done.values() if r.get("status") == "ok"}
    for n, it in enumerate(todo, 1):
        if datetime.now(IST) >= stop:
            print(f"reached {args.until} IST — stopping; rerun to continue")
            break
        row = {**it, "file": "", "content_type": "", "bytes": "", "md5": "", "same_as": "",
               "fetched_at": datetime.now(IST).isoformat(timespec="seconds")}
        try:
            r = s.get(it["source_url"], timeout=90)
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            if r.status_code == 200 and (ctype.startswith(("image/", "video/")) or ctype == "application/pdf") and r.content:
                name = f"{it['stem']}.{EXT.get(ctype, ctype.split('/')[-1])}"
                (img_dir / name).write_bytes(r.content)
                md5 = hashlib.md5(r.content).hexdigest()
                row.update(file=f"images/{name}", content_type=ctype, bytes=len(r.content), md5=md5,
                           same_as=by_md5.get(md5, ""), status="ok")
                by_md5.setdefault(md5, row["file"])
                fails = 0
            else:
                row.update(content_type=ctype, status=f"no image (HTTP {r.status_code}, {ctype or 'no type'})")
                fails += 1
        except Exception as exc:  # noqa: BLE001 — record and carry on
            row.update(status=f"failed: {type(exc).__name__}")
            fails += 1
        done[it["source_url"]] = row
        if n % 25 == 0 or n == len(todo):
            save_manifest(mpath, done)
            ok = sum(1 for r in done.values() if r["status"] == "ok")
            print(f"{n}/{len(todo)}  ok so far {ok}", flush=True)
        if fails and fails % 3 == 0:
            print(f"{fails} failures in a row — pausing 5 min", flush=True)
            save_manifest(mpath, done)
            time.sleep(300)
        time.sleep(args.gap)
    save_manifest(mpath, done)
    rows = list(done.values())
    ok = [r for r in rows if r["status"] == "ok"]
    print(f"done: {len(ok)}/{len(items)} images, {sum(int(r['bytes']) for r in ok) / 1e6:.0f} MB; "
          f"{sum(1 for r in ok if r['same_as'])} identical to another; "
          f"{len(rows) - len(ok)} not downloaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
