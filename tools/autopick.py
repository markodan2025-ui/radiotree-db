#!/usr/bin/env python3
"""
Automatski odabir najboljih N stanica po IZMJERENIM kriterijima.
Ne pita te nista. Ne slusa. Sudi po brojkama koje je check.py stvarno izmjerio.

Rezultat je startna lista, ne konacna. Tvoje rucne odluke NIKAD ne dira.

Env:
  PICK_TOTAL      koliko stanica ukupno (default 500)
  PICK_MIN_BR     minimalni izmjereni bitrate za lossy (default 128)
  PICK_MIN_UPTIME minimalni uptime (default 0.8)
  PICK_RESET      "1" = obrisi prethodne auto-odluke prije novog izbora
"""
import json, os, sys, time
from collections import defaultdict
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

TOTAL = int(os.environ.get("PICK_TOTAL", "500"))
MIN_BR = int(os.environ.get("PICK_MIN_BR", "128"))
MIN_UPTIME = float(os.environ.get("PICK_MIN_UPTIME", "0.8"))
RESET = os.environ.get("PICK_RESET", "") == "1"

MAX_PER_HOST = 6          # da ti 40 stanica ne dodje s istog providera
MAX_PER_COUNTRY_GENRE = 8  # da Jazz ne bude 100% americki


def load(n, d):
    p = os.path.join(DATA, n)
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def score(st, summ, last):
    """Sve tezine dolaze iz IZMJERENIH podataka. RB glasovi imaju najmanju tezinu."""
    s = 0.0
    br = last.get("bitrate_kbps") or 0
    if last.get("lossless"):
        s += 45
    elif br >= 320:
        s += 30
    elif br >= 256:
        s += 22
    elif br >= 192:
        s += 15
    elif br >= 128:
        s += 8

    sr = last.get("sample_rate") or 0
    if sr >= 48000:
        s += 6
    elif sr >= 44100:
        s += 4

    if (last.get("channels") or 0) == 2:
        s += 5

    s += 25 * (summ.get("uptime") or 0)

    cm = last.get("connect_ms") or 9999
    if cm < 600:
        s += 8
    elif cm < 1200:
        s += 5
    elif cm < 2500:
        s += 2
    elif cm > 4000:
        s -= 5

    # kazna za laz: deklarirano puno vise od izmjerenog
    dec = last.get("declared_bitrate") or 0
    if dec and br and dec > br * 1.3:
        s -= 12

    # provjerenost: vise provjera = pouzdaniji uptime
    s += min(5, (summ.get("checks") or 0) / 6.0)

    # tvoja rucna lista ima prednost nad scrapeom
    if st.get("source") == "manual" or st.get("manual_confirmed"):
        s += 10

    # popularnost samo kao tie-breaker, gameable je
    s += min(4, (st.get("rb_votes") or 0) / 250.0)
    return s


def host(url):
    try:
        return urlparse(url).netloc.split(":")[0].lower()
    except Exception:
        return url


def main():
    cands = load("candidates.json", {"stations": []})["stations"]
    health = load("health.json", {"stations": {}})["stations"]
    genres = load("genres.json", {"genres": []})["genres"]
    dpath = os.path.join(DATA, "decisions.json")
    ddoc = load("decisions.json", {"decisions": {}})
    dec = ddoc.setdefault("decisions", {})

    if RESET:
        n = len([k for k, v in dec.items() if v.get("auto")])
        for k in [k for k, v in dec.items() if v.get("auto")]:
            del dec[k]
        print(f"Obrisano {n} prethodnih auto-odluka (rucne netaknute)")

    human = {k for k, v in dec.items() if not v.get("auto")}
    print(f"Rucnih odluka koje se ne diraju: {len(human)}")

    # --- 1. tvrda vrata ------------------------------------------------------
    pool = []
    rej = defaultdict(int)
    for st in cands:
        h = health.get(st["id"], {})
        summ = h.get("summary", {})
        last = summ.get("last") or {}
        if not last.get("ok"):
            rej["zadnja provjera pala"] += 1
            continue
        if (summ.get("uptime") or 0) < MIN_UPTIME:
            rej[f"uptime < {int(MIN_UPTIME*100)}%"] += 1
            continue
        if (last.get("channels") or 0) < 2:
            rej["nije stereo"] += 1
            continue
        br = last.get("bitrate_kbps") or 0
        if not last.get("lossless") and br < MIN_BR:
            rej[f"bitrate < {MIN_BR}"] += 1
            continue
        if (last.get("connect_ms") or 0) > 6000:
            rej["spajanje > 6s"] += 1
            continue
        pool.append((score(st, summ, last), st, summ, last))

    pool.sort(key=lambda x: -x[0])
    print(f"\nProslo vrata: {len(pool)} / {len(cands)}")
    for k, v in sorted(rej.items(), key=lambda x: -x[1]):
        print(f"  odbijeno  {k:<24} {v}")

    # --- 2. kvote po zanru ---------------------------------------------------
    weights = {g["id"]: max(1, g.get("target", 0)) for g in genres}
    tot_w = sum(weights.values()) or 1
    quota = {g: max(5, round(TOTAL * w / tot_w)) for g, w in weights.items()}

    picked, per_g, per_host, per_cg = [], defaultdict(int), defaultdict(int), defaultdict(int)
    taken = set()

    for sc, st, summ, last in pool:
        if len(picked) >= TOTAL:
            break
        if st["id"] in taken or st["id"] in human:
            continue
        gl = st.get("genres") or ["other"]
        g = next((x for x in gl if per_g[x] < quota.get(x, 5)), None)
        if not g:
            continue
        hh = host(last.get("final_url") or st.get("url_resolved") or st["url"])
        if per_host[hh] >= MAX_PER_HOST:
            continue
        cg = (st.get("country", "??"), g)
        if per_cg[cg] >= MAX_PER_COUNTRY_GENRE:
            continue
        picked.append((sc, st, g, summ, last))
        taken.add(st["id"])
        per_g[g] += 1
        per_host[hh] += 1
        per_cg[cg] += 1

    # --- 3. popuni ostatak, progresivno popustajuci limit po hostu -----------
    # Bez ovoga tvrdi MAX_PER_HOST tiho zaustavi izbor daleko ispod TOTAL.
    host_cap = MAX_PER_HOST
    while len(picked) < TOTAL and host_cap <= 64:
        added = 0
        for sc, st, summ, last in pool:
            if len(picked) >= TOTAL:
                break
            if st["id"] in taken or st["id"] in human:
                continue
            hh = host(last.get("final_url") or st.get("url_resolved") or st["url"])
            if per_host[hh] >= host_cap:
                continue
            g = (st.get("genres") or ["other"])[0]
            picked.append((sc, st, g, summ, last))
            taken.add(st["id"])
            per_g[g] += 1
            per_host[hh] += 1
            added += 1
        if len(picked) >= TOTAL:
            break
        if added == 0 and host_cap > MAX_PER_HOST:
            break          # bazen je stvarno iscrpljen, nema se sto popustiti
        host_cap *= 2
    if host_cap > MAX_PER_HOST:
        print(f"\nUPOZORENJE: limit po hostu popusten na {host_cap} da se dosegne {TOTAL}.")
        print("  Znaci da ti bazen kandidata nije dovoljno raznolik -> povecaj PER_TAG i fetchaj jos.")

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for sc, st, g, summ, last in picked:
        dec[st["id"]] = {"verdict": "yes", "genres": st.get("genres") or [g],
                         "ts": now, "auto": True, "score": round(sc, 1)}
    ddoc["count"] = len(dec)
    ddoc["generated"] = now
    json.dump(ddoc, open(dpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"\nOdabrano automatski: {len(picked)}")
    gname = {g["id"]: g["name"] for g in genres}
    for g, n in sorted(per_g.items(), key=lambda x: -x[1]):
        print(f"  {gname.get(g, g):<14} {n:>3} / kvota {quota.get(g, '-')}")
    if len(picked) < TOTAL:
        print(f"\nTrazio si {TOTAL}, ali samo {len(picked)} stanica zadovoljava kriterije.")
        print("  Spusti PICK_MIN_BR / PICK_MIN_UPTIME ili fetchaj vise kandidata.")
    top_hosts = sorted(per_host.items(), key=lambda x: -x[1])[:3]
    print("\nNajzastupljeniji hostovi: " + ", ".join(f"{h} ({n})" for h, n in top_hosts))
    ll = sum(1 for p in picked if p[4].get("lossless"))
    avg = sum(p[4].get("bitrate_kbps") or 0 for p in picked) / max(1, len(picked))
    print(f"\nLossless: {ll}   Prosjecni izmjereni bitrate: {int(avg)} kbps")
    print("Sljedece: build_db.py napravi produkcijsku listu + CSV")
    return 0


if __name__ == "__main__":
    sys.exit(main())
