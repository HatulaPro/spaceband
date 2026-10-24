"""Probe the ListenBrainz labs similar-artists endpoint and record what each request shape returns."""
import json
import os
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "raw", "diag.txt")
ALG = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
A, B = "a74b1b7f-71a5-4011-9441-d0b5e4122711", "f59c5520-5f46-4d2c-b2c4-822eabf53419"
BASE = "https://labs.api.listenbrainz.org/similar-artists"


def go(label, url, body=None):
    data = None if body is None else json.dumps(body).encode()
    h = {"User-Agent": "spaceband/0.1", "Accept": "application/json"}
    if data:
        h["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=60) as r:
            raw = r.read().decode("utf-8", "replace")
            st = r.status
    except urllib.error.HTTPError as e:
        raw, st = e.read().decode("utf-8", "replace"), e.code
    except Exception as e:
        raw, st = repr(e), -1
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(f"=== {label}\n{st} len={len(raw)}\n{raw[:700]}\n\n")


open(OUT, "w").close()
go("GET single", f"{BASE}/json?artist_mbids={A}&algorithm={ALG}")
go("GET comma", f"{BASE}/json?artist_mbids={A},{B}&algorithm={ALG}")
go("POST json list", f"{BASE}/json", [{"artist_mbids": A, "algorithm": ALG}, {"artist_mbids": B, "algorithm": ALG}])
go("POST json list with [ ]", f"{BASE}/json", [{"[artist_mbids]": A, "algorithm": ALG}])
go("POST base", BASE, [{"artist_mbids": A, "algorithm": ALG}])
go("POST json single obj", f"{BASE}/json", {"artist_mbids": A, "algorithm": ALG})
print("done, wrote", OUT)
