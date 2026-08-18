#!/usr/bin/env python3
"""
Uvozi TVOJE rucne liste stanica u data/candidates.json.

Sve iz data/import/* se procita, parsira i spoji s postojecim kandidatima.
Rucno uvezene stanice imaju source="manual" i fetch_candidates.py ih NIKAD ne brise.

Podrzani formati (auto-detekcija):
  1. Obicni URL po retku:
       http://stream.example.com/live

  2. Ime | URL | zanrovi:
       Miles Davis Radio | http://stream.example.com/live | jazz,artist

  3. JSON polje:
       [{"name":"X","url":"http://...","genres":["artist"]}]

  4. JS/HTML blok iz aplikacije (izvuce parove ime+url regexom):
       const ARTIST = [ {name:"X", url:"http://..."}, ... ];

Env:
  IMPORT_GENRE    zadani zanr ako redak ne navodi svoj (default: artist)
  IMPORT_VERDICT  ako postavis na yes/maybe, odmah upise odluku (default: prazno)
"""
import json, os, re, sys, time, hashlib
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
IMPORT_DIR = os.path.join(DATA, "import")

DEFAULT_GENRE = os.environ.get("IMPORT_GENRE", "artist").strip() or "artist"
AUTO_VERDICT = os.environ.get("IMPORT_VERDICT", "").strip().lower()

URL_RE = re.compile(r"https?://[^\s\"'<>,;|\\)\]}]+", re.I)


def norm_key(url):
    try:
        p = urlparse(url.strip().lower())
        return f"{p.netloc.split('@')[-1]}{p.path.rstrip('/')}"
    except Exception:
        return url.strip().lower()


def mk_id(url):
    return "man:" + hashlib.sha1(norm_key(url).encode()).hexdigest()[:16]


def clean_name(s):
    s = re.sub(r"\s+", " ", (s or "").strip())
    s = s.strip("\"'`,:;-| \t")
    return s[:120]


def parse_json_blob(txt):
    """Format 3: pravi JSON."""
    try:
        data = json.loads(txt)
    except Exception:
        return None
    rows = data if isinstance(data, list) else data.get("stations") or data.get("items")
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        url = ""
        for k in ("url", "stream", "streamUrl", "stream_url", "url_resolved", "src"):
            if r.get(k):
                url = str(r[k]).strip()
                break
        if not url:
            continue
        name = ""
        for k in ("name", "title", "station", "artist", "label"):
            if r.get(k):
                name = str(r[k]).strip()
                break
        g = r.get("genres") or r.get("genre") or r.get("tags") or []
        if isinstance(g, str):
            g = [x.strip() for x in re.split(r"[,;]", g) if x.strip()]
        out.append({"name": clean_name(name), "url": url,
                    "genres": [x.lower() for x in g] or [DEFAULT_GENRE],
                    "country": (r.get("country") or r.get("countrycode") or "").upper()[:2]})
    return out or None


def parse_lines(txt):
    """Formati 1 i 2."""
    out = []
    for raw in txt.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        if "|" in line or "\t" in line:
            parts = [p.strip() for p in re.split(r"\||\t", line)]
            url = next((p for p in parts if URL_RE.fullmatch(p) or p.lower().startswith("http")), "")
            if not url:
                continue
            rest = [p for p in parts if p != url]
            name = clean_name(rest[0]) if rest else ""
            genres = []
            if len(rest) > 1:
                genres = [x.strip().lower() for x in re.split(r"[,;]", rest[1]) if x.strip()]
            out.append({"name": name, "url": url, "genres": genres or [DEFAULT_GENRE], "country": ""})
        else:
            m = URL_RE.search(line)
            if m:
                out.append({"name": "", "url": m.group(0), "genres": [DEFAULT_GENRE], "country": ""})
    return out or None


def parse_js(txt):
    """Format 4: izvuci svaki URL i najblize ime prije njega."""
    out = []
    for m in URL_RE.finditer(txt):
        url = m.group(0).rstrip(",;")
        window = txt[max(0, m.start() - 220):m.start()]
        names = re.findall(r'["\']([^"\']{2,90})["\']', window)
        name = ""
        for cand in reversed(names):
            if cand.lower().startswith("http") or cand.lower() in (
                    "url", "name", "src", "stream", "title", "genre", "genres"):
                continue
            name = cand
            break
        out.append({"name": clean_name(name), "url": url,
                    "genres": [DEFAULT_GENRE], "country": ""})
    return out or None


def parse_file(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    if not URL_RE.search(txt):
        return []
    for parser in (parse_json_blob, parse_lines, parse_js):
        if parser is parse_lines and path.lower().endswith((".js", ".html", ".htm")):
            continue
        rows = parser(txt)
        if rows:
            print(f"  {os.path.basename(path)}: {parser.__name__} -> {len(rows)} redaka")
            return rows
    return []


def register_genres(genres_used):
    """Ako uvezes zanr koji ne postoji, dodaj ga u taksonomiju umjesto da tiho nestane."""
    gp = os.path.join(DATA, "genres.json")
    doc = json.load(open(gp, encoding="utf-8"))
    known = {g["id"] for g in doc["genres"]}
    added = []
    for gid in sorted(genres_used):
        if gid and gid not in known:
            doc["genres"].append({"id": gid, "name": gid.replace("-", " ").title(),
                                  "tags": [], "target": 0, "manual": True})
            added.append(gid)
    if added:
        json.dump(doc, open(gp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"  novi zanrovi u genres.json: {', '.join(added)}")


def main():
    if not os.path.isdir(IMPORT_DIR):
        os.makedirs(IMPORT_DIR, exist_ok=True)
    files = sorted(f for f in os.listdir(IMPORT_DIR)
                   if not f.startswith(".") and not f.endswith(".md"))
    if not files:
        print(f"Nema datoteka u data/import/. Ubaci listu i pokreni ponovo.")
        return 0

    rows = []
    print("Citam:")
    for f in files:
        rows += parse_file(os.path.join(IMPORT_DIR, f))
    if not rows:
        print("Nijedan URL nije prepoznat.")
        return 1

    cpath = os.path.join(DATA, "candidates.json")
    doc = json.load(open(cpath, encoding="utf-8")) if os.path.exists(cpath) \
        else {"stations": []}
    existing = doc.get("stations", [])
    by_key = {norm_key(s.get("url_resolved") or s["url"]): s for s in existing}

    added = merged = dupe = 0
    seen = set()
    genres_used = set()
    for r in rows:
        key = norm_key(r["url"])
        if key in seen:
            dupe += 1
            continue
        seen.add(key)
        genres_used.update(r["genres"])
        hit = by_key.get(key)
        if hit:
            # vec postoji (npr. iz Radio Browsera) -> samo dopuni, ne dupliciraj
            for g in r["genres"]:
                if g not in hit.setdefault("genres", []):
                    hit["genres"].append(g)
            hit["manual_confirmed"] = True
            if r["name"] and len(r["name"]) > len(hit.get("name", "")):
                hit["name"] = r["name"]
            merged += 1
            continue
        st = {
            "id": mk_id(r["url"]),
            "name": r["name"] or urlparse(r["url"]).netloc,
            "url": r["url"],
            "url_resolved": r["url"],
            "homepage": "",
            "favicon": "",
            "country": r.get("country", ""),
            "language": "",
            "genres": r["genres"],
            "rb_tags": [],
            "rb_codec": "",
            "rb_bitrate": 0,
            "rb_votes": 0,
            "rb_clicks": 0,
            "source": "manual",
        }
        existing.append(st)
        by_key[key] = st
        added += 1

    doc["stations"] = existing
    doc["count"] = len(existing)
    doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    json.dump(doc, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    register_genres(genres_used)

    if AUTO_VERDICT in ("yes", "maybe", "no"):
        dpath = os.path.join(DATA, "decisions.json")
        dd = json.load(open(dpath, encoding="utf-8")) if os.path.exists(dpath) else {"decisions": {}}
        dec = dd.setdefault("decisions", {})
        n = 0
        for s in existing:
            if s.get("source") == "manual" and s["id"] not in dec:
                dec[s["id"]] = {"verdict": AUTO_VERDICT, "genres": s["genres"],
                                "ts": doc["updated"], "auto": True}
                n += 1
        dd["count"] = len(dec)
        json.dump(dd, open(dpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"  auto-verdict '{AUTO_VERDICT}' postavljen na {n} stanica")

    print(f"\nNovih: {added}   Spojenih s postojecima: {merged}   Duplikata preskoceno: {dupe}")
    print(f"Ukupno kandidata: {len(existing)}")
    print("\nSljedece: pokreni 'Refresh station database' (fetch = false) da ih izmjeri.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
