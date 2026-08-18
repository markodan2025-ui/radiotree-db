#!/usr/bin/env python3
"""
Spaja: candidates (stroj) + health (stroj) + decisions (TI) -> db/v1/ produkcijska baza.
U produkciju ide SAMO ono sto si ti rekao YES i sto je zdravo.
"""
import json, os, sys, time, hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "db", "v1")


def load(name, default):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def main():
    cands = load("candidates.json", {"stations": []})["stations"]
    health = load("health.json", {"stations": {}})["stations"]
    decisions = load("decisions.json", {"decisions": {}})["decisions"]
    genres = load("genres.json", {"genres": []})["genres"]
    os.makedirs(OUT, exist_ok=True)

    out_stations = []
    stats = {}
    for st in cands:
        d = decisions.get(st["id"], {})
        if d.get("verdict") != "yes":
            continue
        h = health.get(st["id"], {})
        summ = h.get("summary", {})
        if summ.get("status") == "down":
            continue                       # nikad ne saljemo mrtvu stanicu na TV
        if d.get("disabled"):
            continue
        last = summ.get("last") or {}
        gl = d.get("genres") or st.get("genres") or []
        row = {
            "id": st["id"],
            "name": (d.get("name") or last.get("icy_name") or st["name"]).strip(),
            "url": last.get("final_url") or st.get("url_resolved") or st["url"],
            "country": st.get("country", ""),
            "genres": gl,
            "codec": last.get("codec", ""),
            "bitrate": last.get("bitrate_kbps", 0),
            "sample_rate": last.get("sample_rate", 0),
            "channels": last.get("channels", 0),
            "lossless": bool(last.get("lossless")),
            "uptime": summ.get("uptime", 0),
            "homepage": st.get("homepage", ""),
        }
        if d.get("note"):
            row["note"] = d["note"]
        out_stations.append(row)
        for g in gl:
            stats[g] = stats.get(g, 0) + 1

    out_stations.sort(key=lambda s: (s["genres"][0] if s["genres"] else "zzz", s["name"].lower()))

    genre_rows = [{"id": g["id"], "name": g["name"], "count": stats.get(g["id"], 0)} for g in genres]

    # Hash NAMJERNO ne ukljucuje timestamp. Inace bi svaki dnevni run
    # bumpao verziju i natjerao svaki TV da ponovo skine cijelu bazu bez razloga.
    content = json.dumps({"genres": genre_rows, "stations": out_stations},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

    prev_ver = 0
    mp = os.path.join(OUT, "manifest.json")
    if os.path.exists(mp):
        try:
            prev = json.load(open(mp, encoding="utf-8"))
            prev_ver = prev.get("version", 0) or 0
            if prev.get("sha256") == digest:
                print(f"Baza nepromijenjena ({len(out_stations)} stanica), verzija ostaje v{prev_ver}. Nista se ne commita.")
                return
        except Exception:
            pass

    body = {
        "schema": 1,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": prev_ver + 1,
        "count": len(out_stations),
        "genres": genre_rows,
        "stations": out_stations,
    }
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    sp = os.path.join(OUT, "stations.json")
    with open(sp, "w", encoding="utf-8") as f:
        f.write(payload)

    manifest = {
        "schema": 1,
        "version": body["version"],
        "generated": body["generated"],
        "count": len(out_stations),
        "sha256": digest,
        "bytes": len(payload.encode("utf-8")),
        "stations_url": "stations.json",
        "genres": body["genres"],
    }
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    # CSV za ljudsko citanje: ime, link, bitrate, zanr, zemlja
    import csv
    gname = {g["id"]: g["name"] for g in genres}
    cp = os.path.join(OUT, "stations.csv")
    with open(cp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "url", "codec", "bitrate_kbps", "sample_rate",
                    "lossless", "genre", "country", "uptime"])
        for r in out_stations:
            w.writerow([r["name"], r["url"], r["codec"], r["bitrate"], r["sample_rate"],
                        "yes" if r["lossless"] else "no",
                        "; ".join(gname.get(g, g) for g in r["genres"]),
                        r["country"], f"{r['uptime']:.2f}"])

    print(f"db/v1/stations.json  {len(out_stations)} stanica, {manifest['bytes']} B, v{manifest['version']}")
    print(f"db/v1/stations.csv   isto, za ljudsko citanje")
    for g in body["genres"]:
        print(f"  {g['name']:<14} {g['count']}")


if __name__ == "__main__":
    sys.exit(main())
