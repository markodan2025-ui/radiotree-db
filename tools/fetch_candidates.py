#!/usr/bin/env python3
"""
Povlaci kandidate iz Radio Browsera i sprema ih u data/candidates.json.
NIKAD ne dira data/decisions.json (tvoje ljudske odluke) niti data/health.json.
"""
import json, os, re, sys, time, hashlib
from urllib.parse import urlparse
import requests

UA = "RadioTree/1.0 (+https://radiotree.app)"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# stanice s ovim rijecima u imenu su gotovo uvijek smece / duplikati / testovi
BLACKLIST_NAME = re.compile(
    r"(test\s*stream|^test$|xxx|porn|sex\s*radio|\bspam\b|localhost|127\.0\.0\.1)", re.I
)


def pick_server():
    """Radio Browser trazi da otkrijes zivi mirror, ne da hardkodiras jedan."""
    try:
        r = requests.get("https://all.api.radio-browser.info/json/servers",
                         headers={"User-Agent": UA}, timeout=15)
        r.raise_for_status()
        names = sorted({s["name"] for s in r.json()})
        if names:
            return "https://" + names[0]
    except Exception as e:
        print(f"  ! discovery pao ({e}), koristim de1 fallback")
    return "https://de1.api.radio-browser.info"


def norm_key(url):
    """Kljuc za deduplikaciju: host + path bez query stringa i bez trailing slasha."""
    try:
        p = urlparse(url.strip().lower())
        host = p.netloc.split("@")[-1]
        path = p.path.rstrip("/")
        return f"{host}{path}"
    except Exception:
        return url.strip().lower()


def station_id(st):
    uuid = st.get("stationuuid")
    if uuid:
        return "rb:" + uuid
    return "h:" + hashlib.sha1(norm_key(st.get("url", "")).encode()).hexdigest()[:16]


def acceptable(st):
    name = (st.get("name") or "").strip()
    url = (st.get("url_resolved") or st.get("url") or "").strip()
    if not name or not url:
        return False
    if not url.startswith(("http://", "https://")):
        return False
    if BLACKLIST_NAME.search(name):
        return False
    codec = (st.get("codec") or "").upper()
    bitrate = st.get("bitrate") or 0
    # FLAC/lossless pustamo bez obzira na prijavljeni bitrate (RB ga cesto pise 0)
    if codec in ("FLAC", "ALAC", "WAV", "PCM"):
        return True
    # sve ispod 64 kbps nema sto traziti u audiophile aplikaciji
    if bitrate and bitrate < 64:
        return False
    return True


def fetch_tag(server, tag, limit):
    url = f"{server}/json/stations/search"
    params = {
        "tagList": tag,
        "limit": limit,
        "hidebroken": "true",
        "order": "clickcount",
        "reverse": "true",
    }
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=45)
    r.raise_for_status()
    return r.json()


def main():
    per_tag = int(os.environ.get("PER_TAG", "150"))
    genres = json.load(open(os.path.join(DATA, "genres.json")))["genres"]
    server = pick_server()
    print(f"Radio Browser mirror: {server}")

    by_id = {}
    seen_keys = {}

    for g in genres:
        found = 0
        for tag in g["tags"]:
            try:
                rows = fetch_tag(server, tag, per_tag)
            except Exception as e:
                print(f"  ! {g['id']}/{tag}: {e}")
                continue
            for st in rows:
                if not acceptable(st):
                    continue
                sid = station_id(st)
                key = norm_key(st.get("url_resolved") or st.get("url"))
                if key in seen_keys and seen_keys[key] != sid:
                    # isti stream pod drugim imenom -> preskoci, ali zapamti zanr
                    prev = by_id.get(seen_keys[key])
                    if prev and g["id"] not in prev["genres"]:
                        prev["genres"].append(g["id"])
                    continue
                seen_keys[key] = sid
                if sid in by_id:
                    if g["id"] not in by_id[sid]["genres"]:
                        by_id[sid]["genres"].append(g["id"])
                    continue
                by_id[sid] = {
                    "source": "radio-browser",
                    "id": sid,
                    "name": (st.get("name") or "").strip()[:120],
                    "url": (st.get("url") or "").strip(),
                    "url_resolved": (st.get("url_resolved") or st.get("url") or "").strip(),
                    "homepage": (st.get("homepage") or "").strip(),
                    "favicon": (st.get("favicon") or "").strip(),
                    "country": (st.get("countrycode") or "").strip().upper(),
                    "language": (st.get("language") or "").strip(),
                    "genres": [g["id"]],
                    "rb_tags": [t for t in (st.get("tags") or "").split(",") if t][:12],
                    "rb_codec": (st.get("codec") or "").upper(),
                    "rb_bitrate": st.get("bitrate") or 0,
                    "rb_votes": st.get("votes") or 0,
                    "rb_clicks": st.get("clickcount") or 0,
                }
                found += 1
            time.sleep(0.4)  # pristojnost prema besplatnom API-ju
        print(f"  {g['id']:<11} +{found}")

    # --- MERGE, ne overwrite -------------------------------------------------
    # Rucno uvezene stanice (source="manual") i sve tvoje odluke moraju prezivjeti
    # svaki fetch. Prepisivanje cijelog candidates.json bi ti pojelo rucnu listu.
    path = os.path.join(DATA, "candidates.json")
    kept_manual = 0
    if os.path.exists(path):
        try:
            prev = json.load(open(path, encoding="utf-8")).get("stations", [])
        except Exception:
            prev = []
        prev_keys = set()
        for st in prev:
            k = norm_key(st.get("url_resolved") or st.get("url", ""))
            prev_keys.add(k)
            if st.get("source") == "manual" or st.get("manual_confirmed"):
                # rucna stanica ima prednost; ako je RB nasao isti stream, spoji zanrove
                dup = next((v for v in by_id.values()
                            if norm_key(v.get("url_resolved") or v["url"]) == k), None)
                if dup:
                    for g in dup.get("genres", []):
                        if g not in st.setdefault("genres", []):
                            st["genres"].append(g)
                    by_id.pop(dup["id"], None)
                by_id[st["id"]] = st
                kept_manual += 1
            elif st["id"] not in by_id and st.get("sticky"):
                by_id[st["id"]] = st

    out = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": server,
        "count": len(by_id),
        "stations": sorted(by_id.values(),
                           key=lambda s: (-(s.get("rb_votes") or 0), s["name"].lower())),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\nUkupno kandidata: {len(by_id)} (od toga rucnih zadrzano: {kept_manual}) -> {path}")


if __name__ == "__main__":
    sys.exit(main())
