"""
Download the ListenBrainz artist-similarity graph to data/raw/.

ListenBrainz does not publish artist similarity as a bulk dump file; it is
served by the ListenBrainz "labs" dataset hoster
(https://labs.api.listenbrainz.org/similar-artists), computed from the full
ListenBrainz listen history (session-based co-listening).  This script
mirrors it locally:

  1. seeds   - top artists from the sitewide statistics API (several ranges)
  2. similar - breadth-first crawl of the similar-artists dataset, always
               expanding the most-referenced unvisited artist first, so the
               popular "core" of the map is downloaded before the long tail
  3. popularity - total listen / listener counts per artist

Everything is appended to JSONL files, so the crawl can be stopped (Ctrl+C)
and resumed at any time; re-running continues where it left off.

Usage:
    python pipeline/crawl.py                 # default: 25,000 artists
    python pipeline/crawl.py --max 60000     # grow the map later
"""
import argparse
import concurrent.futures
import heapq
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")

API = "https://api.listenbrainz.org/1"
LABS = "https://labs.api.listenbrainz.org/similar-artists/json"
ALGORITHM = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
UA = "spaceband/0.1 (artist map; https://listenbrainz.org)"


def http(url, body=None, retries=6):
    data = None if body is None else json.dumps(body).encode()
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    delay = 2.0
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                if r.status == 204 or not raw.strip():
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                wait = float(e.headers.get("X-RateLimit-Reset-In", delay))
                print(f"  http {e.code}, retrying in {wait:.0f}s", flush=True)
                time.sleep(max(wait, 1))
                delay *= 2
                continue
            if e.code == 400:
                return {"__error__": 400, "body": e.read().decode("utf-8", "replace")[:300]}
            raise
        except json.JSONDecodeError:
            print(f"  unreadable response from {url[:80]}, retrying in {delay:.0f}s", flush=True)
            time.sleep(delay)
            delay *= 2
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            print(f"  network error {e}, retrying in {delay:.0f}s", flush=True)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"giving up on {url}")


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # partial line from an interrupted run
    return out


# ---------------------------------------------------------------- seeds
def fetch_seeds():
    path = os.path.join(RAW, "seeds.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    seeds = {}
    for rng in ["all_time", "year", "half_yearly", "quarter", "month"]:
        for offset in range(0, 1000, 100):
            try:
                js = http(f"{API}/stats/sitewide/artists?range={rng}&count=100&offset={offset}", retries=3)
            except RuntimeError:
                js = None
            if not js or "payload" not in js:
                break
            artists = js["payload"]["artists"]
            for a in artists:
                mbid = a.get("artist_mbid")
                if mbid and mbid not in seeds:
                    seeds[mbid] = {"name": a["artist_name"], "listen_count": a["listen_count"]}
            if len(artists) < 100:
                break
        print(f"seeds after '{rng}': {len(seeds)}", flush=True)
    json.dump(seeds, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return seeds


# ---------------------------------------------------------------- similar
def get_one(m):
    r = http(f"{LABS}?artist_mbids={m}&algorithm={ALGORITHM}")
    rows, seen = [], set()
    for x in r if isinstance(r, list) else []:
        sm = x.get("artist_mbid")
        if x.get("reference_mbid") == m and sm and sm not in seen and sm != m:
            seen.add(sm)
            rows.append([sm, x.get("name"), x.get("score"), x.get("type")])
    return rows


POOL = None


def query_similar(mbids):
    """Return {mbid: [rows]}: one request per artist, a few in parallel.

    (The dataset hoster also accepts a list of mbids in one POST, but then it
    answers for the group as a whole, which mixes the artists' lists together.)"""
    return dict(zip(mbids, POOL.map(get_one, mbids)))


def crawl(max_artists, batch):
    seeds = fetch_seeds()
    path = os.path.join(RAW, "similar.jsonl")
    done = {}
    names = {m: s["name"] for m, s in seeds.items()}
    refs = {}  # mbid -> number of times referenced (priority)
    for rec in read_jsonl(path):
        done[rec["mbid"]] = True
        for m, name, score, _t in rec["similar"]:
            names.setdefault(m, name)
            refs[m] = refs.get(m, 0) + 1

    # priority queue: seeds first (by listen count), then most-referenced
    heap = []
    for m, s in seeds.items():
        if m not in done:
            heapq.heappush(heap, (-1e12 - s["listen_count"], m))
    for m, c in refs.items():
        if m not in done and m not in seeds:
            heapq.heappush(heap, (-c, m))

    print(f"already downloaded: {len(done)}  frontier: {len(heap)}  target: {max_artists}", flush=True)
    t0 = time.time()
    n0 = len(done)
    with open(path, "a", encoding="utf-8") as f:
        while heap and len(done) < max_artists:
            picked = []
            while heap and len(picked) < batch:
                prio, m = heapq.heappop(heap)
                if m in done or m in picked:
                    continue
                # stale entry: priority went up since push; re-push lazily
                cur = refs.get(m, 0)
                if prio > -1e12 and -prio < cur:
                    heapq.heappush(heap, (-cur, m))
                    continue
                picked.append(m)
            if not picked:
                break
            res = query_similar(picked)
            for m in picked:
                rows = res.get(m, [])
                f.write(json.dumps({"mbid": m, "name": names.get(m), "similar": rows}, ensure_ascii=False) + "\n")
                done[m] = True
                for sm, sname, _s, _t in rows:
                    names.setdefault(sm, sname)
                    refs[sm] = refs.get(sm, 0) + 1
                    if sm not in done:
                        heapq.heappush(heap, (-refs[sm], sm))
            f.flush()
            n = len(done)
            if n - n0 <= 3 * batch:
                got = sum(len(res.get(m, [])) for m in picked)
                print(f"  batch of {len(picked)}: {got} similar-artist rows", flush=True)
            if n // 500 != (n - len(picked)) // 500:
                rate = (n - n0) / max(time.time() - t0, 1e-6)
                eta = (max_artists - n) / max(rate, 1e-6)
                print(f"  {n:>7} artists  {rate:5.1f}/s  eta {eta/60:5.1f} min", flush=True)
    print(f"similar: {len(done)} artists downloaded", flush=True)
    return list(done.keys())


# ---------------------------------------------------------------- popularity
def fetch_popularity(mbids, batch=500):
    path = os.path.join(RAW, "popularity.jsonl")
    have = {r["artist_mbid"] for r in read_jsonl(path)}
    todo = [m for m in mbids if m not in have]
    print(f"popularity: {len(have)} cached, {len(todo)} to fetch", flush=True)
    with open(path, "a", encoding="utf-8") as f:
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            js = http(f"{API}/popularity/artist", body={"artist_mbids": chunk})
            if isinstance(js, dict) and "__error__" in js:
                js = []
                for m in chunk:
                    r = http(f"{API}/popularity/artist", body={"artist_mbids": [m]})
                    if isinstance(r, list):
                        js.extend(r)
            for r in js or []:
                f.write(json.dumps({"artist_mbid": r.get("artist_mbid"),
                                    "listens": r.get("total_listen_count"),
                                    "users": r.get("total_user_count")}) + "\n")
            f.flush()
            if (i // batch) % 10 == 0:
                print(f"  popularity {i + len(chunk)}/{len(todo)}", flush=True)


class Tee:
    """Mirror console output to data/raw/crawl.log so progress can be checked from elsewhere."""
    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8")
        self.out = sys.stdout

    def write(self, s):
        self.out.write(s)
        self.f.write(s)
        self.f.flush()

    def flush(self):
        self.out.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=25000, help="number of artists to download")
    ap.add_argument("--workers", type=int, default=6, help="parallel requests")
    ap.add_argument("--skip-popularity", action="store_true")
    a = ap.parse_args()
    os.makedirs(RAW, exist_ok=True)
    sys.stdout = Tee(os.path.join(RAW, "crawl.log"))
    print(time.strftime("--- %Y-%m-%d %H:%M:%S crawl started"), flush=True)
    global POOL
    POOL = concurrent.futures.ThreadPoolExecutor(max_workers=a.workers)
    mbids = crawl(a.max, a.workers * 4)
    if not a.skip_popularity:
        fetch_popularity(mbids)
    print("done. next: python pipeline/build.py", flush=True)


if __name__ == "__main__":
    sys.exit(main())
