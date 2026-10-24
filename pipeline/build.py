"""
Turn the downloaded similarity graph (data/raw) into the map the UI loads
(web/data/map.json).

  graph    : symmetric weighted artist graph from the similar-artists lists
  layout   : UMAP of each artist's (normalised) neighbourhood vector, in 3D
             and 2D -> artists that share listeners end up close together
  clusters : Louvain communities on the graph ("scenes" / genres)
  colours  : each community gets a hue; hues are arranged on the colour
             wheel by how connected communities are, so neighbouring
             scenes get neighbouring hues. Each artist's hue is then the
             weighted circular mean of its neighbours' hues, so artists
             that bridge two scenes get an in-between colour.
  labels   : optional genre names per cluster from MusicBrainz tags of the
             cluster's most popular artists (cached in data/raw/mb_tags.jsonl)

Usage:
    python pipeline/build.py            # with MusicBrainz genre labels
    python pipeline/build.py --no-tags  # offline
"""
import argparse
import collections
import json
import math
import os
import sys
import time
import urllib.request

import numpy as np
import scipy.sparse as sp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "web", "data")


def read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


# ------------------------------------------------------------------ graph
# MusicBrainz "special purpose" artists: placeholders, not real acts
SPECIAL = {"[unknown]", "various artists", "[anonymous]", "[no artist]", "[traditional]", "[dialogue]",
           "[data]", "[nature sounds]", "[church chimes]", "[language instruction]", "[christmas music]",
           "[theme music]", "[soundtrack]", "[mashup]", "[disney]", "[unknown artist]", "[classical]"}
SPECIAL_MBIDS = {"125ec42a-7229-4250-afc5-e057484327fe", "89ad4ac3-39f7-470e-963a-56509c546377",
                 "f731ccc4-e22a-43af-a747-64213329e088", "33cf029c-63b0-41a0-9855-be2a3665fb3b",
                 "9be7f096-97ec-4615-8957-8d40b5dcbc41", "314e1c25-dde7-4e4d-b2f4-0a7b9f7c56dc"}


def load_graph(min_component):
    recs = read_jsonl(os.path.join(RAW, "similar.jsonl"))
    seeds = {}
    sp_path = os.path.join(RAW, "seeds.json")
    if os.path.exists(sp_path):
        seeds = json.load(open(sp_path, encoding="utf-8"))
    names = {}
    for r in recs:
        if r.get("name"):
            names[r["mbid"]] = r["name"]
        for m, n, _s, _t in r["similar"]:
            names.setdefault(m, n)
    for m, s in seeds.items():
        names.setdefault(m, s["name"])

    def special(m):
        n = (names.get(m) or "").strip()
        # MusicBrainz placeholders look like "[unknown]", "[theatre]": bracketed and all lowercase
        bracketed = n.startswith("[") and n.endswith("]") and n == n.lower()
        return m in SPECIAL_MBIDS or n.lower() in SPECIAL or bracketed

    recs = [r for r in recs if not special(r["mbid"])]
    fetched = [r["mbid"] for r in recs]
    idx = {m: i for i, m in enumerate(fetched)}
    rows, cols, vals = [], [], []
    directed = {}
    for r in recs:
        i = idx[r["mbid"]]
        best = {}
        for m, _n, sc, _t in r["similar"]:
            if m in idx and m != r["mbid"] and sc:
                best[idx[m]] = max(best.get(idx[m], 0), sc)
        sims = list(best.items())
        if not sims:
            continue
        top = max(s for _, s in sims)
        directed[i] = [j for j, _ in sorted(sims, key=lambda x: -x[1])]
        for j, s in sims:
            rows.append(i); cols.append(j); vals.append(s / top)
    n = len(fetched)
    W = sp.csr_matrix((vals, (rows, cols)), shape=(n, n))
    W = W.maximum(W.T).tocsr()  # symmetric: similar if either side says so

    # keep connected components that are big enough to be meaningful
    ncomp, lab = sp.csgraph.connected_components(W, directed=False)
    sizes = np.bincount(lab)
    keep = np.where(sizes[lab] >= min_component)[0]
    print(f"graph: {n} artists, {W.nnz // 2} edges, {ncomp} components; keeping {len(keep)}")
    remap = -np.ones(n, dtype=np.int64)
    remap[keep] = np.arange(len(keep))
    W = W[keep][:, keep].tocsr()
    mbids = [fetched[i] for i in keep]
    nbrs = []
    for i in keep:
        nbrs.append([int(remap[j]) for j in directed.get(i, []) if remap[j] >= 0])
    return W, mbids, [names.get(m) or "?" for m in mbids], nbrs


def load_popularity(mbids):
    pop = {r["artist_mbid"]: r for r in read_jsonl(os.path.join(RAW, "popularity.jsonl"))}
    users = np.array([(pop.get(m, {}).get("users") or 0) for m in mbids], dtype=np.float64)
    listens = np.array([(pop.get(m, {}).get("listens") or 0) for m in mbids], dtype=np.float64)
    return users, listens


# ------------------------------------------------------------------ layout
def embed(W, dims, seed, n_neighbors):
    import umap
    from sklearn.preprocessing import normalize
    # each artist described by its weighted neighbourhood (plus itself):
    # cosine similarity of these vectors = "how much do their audiences overlap"
    X = normalize(W + sp.identity(W.shape[0], format="csr") * 1.0, norm="l2")
    reducer = umap.UMAP(n_components=dims, n_neighbors=n_neighbors, min_dist=0.25 if dims == 3 else 0.15,
                        spread=1.2, metric="cosine", random_state=seed, low_memory=True, verbose=False)
    Y = reducer.fit_transform(X)
    Y = Y - np.median(Y, axis=0)
    r = np.percentile(np.linalg.norm(Y, axis=1), 98)
    return (Y / r * 100.0).astype(np.float32)


# ------------------------------------------------------------------ clusters
def communities(W, seed, resolution, min_size):
    import networkx as nx
    G = nx.from_scipy_sparse_array(W)
    comms = nx.community.louvain_communities(G, weight="weight", resolution=resolution, seed=seed)
    lab = np.empty(W.shape[0], dtype=np.int64)
    for c, members in enumerate(sorted(comms, key=len, reverse=True)):
        lab[list(members)] = c
    # fold tiny communities into the community they connect to most
    for _ in range(5):
        sizes = np.bincount(lab)
        small = np.where(sizes[lab] < min_size)[0]
        if len(small) == 0:
            break
        for i in small:
            s, e = W.indptr[i], W.indptr[i + 1]
            js, ws = W.indices[s:e], W.data[s:e]
            score = collections.Counter()
            for j, w in zip(js, ws):
                if sizes[lab[j]] >= min_size:
                    score[lab[j]] += w
            if score:
                lab[i] = score.most_common(1)[0][0]
    # relabel 0..k-1 by size
    uniq, lab = np.unique(lab, return_inverse=True)
    order = np.argsort(-np.bincount(lab))
    rank = np.empty_like(order); rank[order] = np.arange(len(order))
    return rank[lab]


def community_hues(W, lab, pos):
    """Place communities on the colour wheel so connected ones are adjacent."""
    k = lab.max() + 1
    P = sp.csr_matrix((np.ones(len(lab)), (lab, np.arange(len(lab)))), shape=(k, len(lab)))
    C = (P @ W @ P.T).toarray()
    np.fill_diagonal(C, 0)
    if k < 4:
        return np.linspace(0, 360, k, endpoint=False)
    d = C.sum(1) + 1e-9
    Dm = np.diag(1 / np.sqrt(d))
    M = Dm @ C @ Dm
    vals, vecs = np.linalg.eigh(M)
    v = vecs[:, -3:-1] * (1 / np.sqrt(d))[:, None]  # 2 leading non-trivial
    ang = np.arctan2(v[:, 0] - np.median(v[:, 0]), v[:, 1] - np.median(v[:, 1]))
    # spread evenly in angle order (keeps adjacency, avoids crowded hues)
    order = np.argsort(ang)
    hues = np.empty(k)
    hues[order] = np.linspace(0, 360, k, endpoint=False)
    return hues


def oklch_to_srgb(L, C, h):
    hr = np.radians(h)
    a, b = C * np.cos(hr), C * np.sin(hr)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    rgb = np.stack([r, g, bb], -1)
    rgb = np.clip(rgb, 0, 1)
    rgb = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
    return np.clip(np.round(rgb * 255), 0, 255).astype(np.uint8)


def artist_colours(W, lab, hue_c, users):
    # neighbouring scenes on the wheel alternate slightly in lightness so they stay distinguishable
    rank = np.argsort(np.argsort(hue_c))
    l_off = np.where(rank % 2 == 0, 0.04, -0.04)[lab]
    hues = np.radians(hue_c[lab])
    z = np.exp(1j * hues)
    # smooth: own community counts double, plus neighbours weighted by similarity
    Wn = sp.diags(1 / (np.asarray(W.sum(1)).ravel() + 1e-9)) @ W
    zz = 2 * z + Wn @ z
    h = np.degrees(np.angle(zz)) % 360
    purity = np.clip(np.abs(zz) / 3, 0, 1)  # bridges between scenes get softer chroma
    p = np.log1p(users)
    p = (p - p.min()) / (p.max() - p.min() + 1e-9)
    L = 0.6 + 0.18 * p + l_off
    C = 0.08 + 0.12 * purity
    return oklch_to_srgb(L, C, h), h


# ------------------------------------------------------------------ genres
def mb_tags(mbids, cache_path, limit_s=1.1):
    cache = {r["mbid"]: r for r in read_jsonl(cache_path)}
    todo = [m for m in mbids if m not in cache]
    if todo:
        print(f"musicbrainz genres: fetching {len(todo)} artists (~{len(todo) * limit_s / 60:.1f} min)")
    with open(cache_path, "a", encoding="utf-8") as f:
        for n, m in enumerate(todo):
            url = f"https://musicbrainz.org/ws/2/artist/{m}?inc=genres+tags&fmt=json"
            req = urllib.request.Request(url, headers={"User-Agent": "spaceband/0.1 ( artist map )"})
            rec = {"mbid": m, "genres": [], "tags": [], "country": None, "type": None}
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(req, timeout=30) as r:
                        js = json.loads(r.read().decode())
                    rec["genres"] = [[g["name"], g.get("count", 0)] for g in js.get("genres", [])]
                    rec["tags"] = [[g["name"], g.get("count", 0)] for g in js.get("tags", [])]
                    rec["country"] = js.get("country")
                    rec["type"] = js.get("type")
                    break
                except Exception as e:  # 503 = rate limited, 404 = merged/removed
                    if "404" in str(e):
                        break
                    time.sleep(2 + attempt * 2)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            cache[m] = rec
            time.sleep(limit_s)
            if n % 100 == 99:
                print(f"  {n + 1}/{len(todo)}")
    return cache


def cluster_labels(lab, users, mbids, names, use_tags, per_cluster):
    k = lab.max() + 1
    tops = []
    for c in range(k):
        mem = np.where(lab == c)[0]
        tops.append(mem[np.argsort(-users[mem])][:per_cluster])
    genres = [[] for _ in range(k)]
    tagcache = {}
    if use_tags:
        want = [mbids[i] for t in tops for i in t]
        try:
            tagcache = mb_tags(want, os.path.join(RAW, "mb_tags.jsonl"))
        except Exception as e:
            print(f"  (genre labels skipped: {e})")
        # genre frequency per cluster vs. overall -> distinctive genre words
        overall = collections.Counter()
        per = []
        for c in range(k):
            cnt = collections.Counter()
            for rank, i in enumerate(tops[c]):
                rec = tagcache.get(mbids[i])
                if not rec:
                    continue
                gl = rec["genres"] or rec["tags"][:5]
                for g, w in gl:
                    cnt[g] += (1 + min(w, 10) / 10) / (1 + rank * 0.15)
            per.append(cnt)
            overall.update({g: 1 for g in cnt})
        for c in range(k):
            sc = {g: v / math.sqrt(overall[g]) for g, v in per[c].items()}
            genres[c] = [g for g, _ in sorted(sc.items(), key=lambda x: -x[1])[:3]]
    return tops, genres, tagcache


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tags", action="store_true", help="skip MusicBrainz genre lookup")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--neighbors", type=int, default=20)
    ap.add_argument("--resolution", type=float, default=2.0, help="higher = more, smaller scenes")
    ap.add_argument("--min-cluster", type=int, default=25)
    ap.add_argument("--min-component", type=int, default=10)
    ap.add_argument("--tag-artists", type=int, default=8, help="top artists per cluster to look up genres for")
    ap.add_argument("--max-nbrs", type=int, default=40)
    a = ap.parse_args()

    t0 = time.time()
    W, mbids, names, nbrs = load_graph(a.min_component)
    users, listens = load_popularity(mbids)
    n = len(mbids)

    print("layout 3D ..."); pos3 = embed(W, 3, a.seed, a.neighbors)
    print("layout 2D ..."); pos2 = embed(W, 2, a.seed, a.neighbors)
    print("clusters ...")
    lab = communities(W, a.seed, a.resolution, a.min_cluster)
    hue_c = community_hues(W, lab, pos3)
    rgb, hue = artist_colours(W, lab, hue_c, users)
    tops, genres, tagcache = cluster_labels(lab, users, mbids, names, not a.no_tags, a.tag_artists)
    print(f"{lab.max() + 1} clusters")

    clusters = []
    for c in range(lab.max() + 1):
        mem = np.where(lab == c)[0]
        w = np.log1p(users[mem]) + 1
        ccol = oklch_to_srgb(np.array([0.75]), np.array([0.16]), np.array([hue_c[c]]))[0]
        clusters.append({
            "size": int(len(mem)),
            "top": [int(i) for i in tops[c]],
            "genres": genres[c],
            "color": "#%02x%02x%02x" % tuple(int(x) for x in ccol),
            "c3": [round(float(v), 2) for v in (pos3[mem] * w[:, None]).sum(0) / w.sum()],
            "c2": [round(float(v), 2) for v in (pos2[mem] * w[:, None]).sum(0) / w.sum()],
        })

    artist_genres = {}
    for m, rec in tagcache.items():
        gl = [g for g, _ in sorted(rec["genres"], key=lambda x: -x[1])][:4]
        if gl:
            artist_genres[m] = gl
    order_pop = np.argsort(-users)
    out = {
        "meta": {
            "source": "ListenBrainz similar-artists (labs.api.listenbrainz.org) + popularity API",
            "artists": n, "edges": int(W.nnz // 2), "clusters": len(clusters),
            "built": time.strftime("%Y-%m-%d %H:%M"),
        },
        "names": names,
        "mbids": mbids,
        "pos3": [round(float(v), 2) for v in pos3.ravel()],
        "pos2": [round(float(v), 2) for v in pos2.ravel()],
        "rgb": [int(v) for v in rgb.ravel()],
        "users": [int(v) for v in users],
        "listens": [int(v) for v in listens],
        "cluster": [int(v) for v in lab],
        "nbrs": [l[:a.max_nbrs] for l in nbrs],
        "byPop": [int(i) for i in order_pop],
        "clusters": clusters,
        "genres": artist_genres,  # mbid -> MusicBrainz genres (only for looked-up artists)
    }
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "map.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {path} ({os.path.getsize(path) / 1e6:.1f} MB) in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
