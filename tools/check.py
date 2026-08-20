#!/usr/bin/env python3
"""
Stream checker. Ovo je srce projekta.
Za svaku stanicu:
  1. prati redirecte, hvata ICY headere (deklarirano)
  2. ffprobe cita STVARNE audio frameove (izmjereno)
  3. zapisuje rezultat u rolling povijest u data/health.json

Deklarirano != izmjereno. Radio Browser polja codec/bitrate redovito lazu.
Ovaj skript je jedini razlog zasto ce tvoj Audiophile tab biti posten.
"""
import json, os, subprocess, sys, time, random
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

UA = "RadioTree/1.0 (+https://radiotree.app)"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

HISTORY_LEN = 30          # zadnjih 30 provjera po stanici
CONNECT_TIMEOUT = 8       # sekundi za TCP + prvi byte
PROBE_SECONDS = 6         # koliko dugo ffprobe slusa
WORKERS = int(os.environ.get("WORKERS", "20"))

LOSSLESS = {"flac", "alac", "wavpack", "pcm_s16le", "pcm_s24le", "pcm_s16be", "pcm_s24be"}


def icy_probe(url):
    """Otvori konekciju, prati redirecte, procitaj ICY headere. Ne skida cijeli stream."""
    out = {"final_url": url, "icy": {}, "http_status": None, "connect_ms": None,
           "content_type": None, "is_playlist": False}
    t0 = time.time()
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Icy-MetaData": "1"},
                         stream=True, timeout=CONNECT_TIMEOUT, allow_redirects=True)
        out["connect_ms"] = int((time.time() - t0) * 1000)
        out["http_status"] = r.status_code
        out["final_url"] = r.url
        ct = (r.headers.get("Content-Type") or "").lower()
        out["content_type"] = ct
        for k, v in r.headers.items():
            if k.lower().startswith("icy-"):
                out["icy"][k.lower()] = v
        if any(x in ct for x in ("mpegurl", "scpls", "x-pls", "text/html", "xspf")):
            out["is_playlist"] = True
        # procitaj malo bajtova da potvrdis da stvarno tece
        got = 0
        for chunk in r.iter_content(4096):
            got += len(chunk)
            if got >= 8192:
                break
        out["bytes_ok"] = got >= 4096
        r.close()
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        out["bytes_ok"] = False
    return out


def ffprobe(url):
    """Ground truth. Bez ovoga imas samo tudje obecanje o kvaliteti."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format",
        "-user_agent", UA,
        "-rw_timeout", str(CONNECT_TIMEOUT * 1000000),
        "-analyzeduration", str(PROBE_SECONDS * 1000000),
        "-probesize", "2000000",
        "-i", url,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=PROBE_SECONDS + 12)
        if p.returncode != 0 or not p.stdout:
            return None
        j = json.loads(p.stdout.decode("utf-8", "replace"))
    except Exception:
        return None
    audio = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not audio:
        return None
    br = audio.get("bit_rate") or j.get("format", {}).get("bit_rate")
    try:
        br = int(int(br) / 1000)
    except Exception:
        br = 0
    codec = (audio.get("codec_name") or "").lower()
    return {
        "codec": codec,
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels": int(audio.get("channels") or 0),
        "bits_per_sample": int(audio.get("bits_per_raw_sample") or audio.get("bits_per_sample") or 0),
        "bitrate_kbps": br,
        "lossless": codec in LOSSLESS,
        "container": (j.get("format", {}).get("format_name") or "").split(",")[0],
    }


def check_one(st):
    sid = st["id"]
    url = st.get("url_resolved") or st.get("url")
    icy = icy_probe(url)
    probe = None
    if icy.get("http_status") and 200 <= icy["http_status"] < 400 and not icy.get("is_playlist"):
        probe = ffprobe(icy["final_url"])
    elif icy.get("is_playlist"):
        # .pls/.m3u -> pusti ffprobe da ga sam razmota
        probe = ffprobe(icy["final_url"])

    ok = bool(probe) and probe.get("channels", 0) > 0
    declared_br = 0
    try:
        declared_br = int(icy["icy"].get("icy-br") or st.get("rb_bitrate") or 0)
    except Exception:
        declared_br = st.get("rb_bitrate") or 0

    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": ok,
        "http": icy.get("http_status"),
        "connect_ms": icy.get("connect_ms"),
        "final_url": icy.get("final_url"),
        "icy_name": icy["icy"].get("icy-name"),
        "icy_genre": icy["icy"].get("icy-genre"),
        "declared_bitrate": declared_br,
        "error": icy.get("error"),
    }
    if probe:
        rec.update({
            "codec": probe["codec"],
            "sample_rate": probe["sample_rate"],
            "channels": probe["channels"],
            "bits": probe["bits_per_sample"],
            "bitrate_kbps": probe["bitrate_kbps"],
            "lossless": probe["lossless"],
            "container": probe["container"],
        })
    return sid, rec


def summarize(hist):
    recent = hist[-HISTORY_LEN:]
    if not recent:
        return {"status": "unknown", "uptime": 0.0, "fail_streak": 0}
    okc = sum(1 for h in recent if h.get("ok"))
    uptime = round(okc / len(recent), 3)
    streak = 0
    for h in reversed(recent):
        if h.get("ok"):
            break
        streak += 1
    last = recent[-1]
    if last.get("ok") and uptime >= 0.9:
        status = "healthy"
    elif streak >= 3:
        status = "down"
    elif last.get("ok"):
        status = "unstable"
    else:
        status = "unstable" if uptime >= 0.5 else "down"
    return {
        "status": status,
        "uptime": uptime,
        "fail_streak": streak,
        "checks": len(recent),
        "last_ok": next((h["ts"] for h in reversed(recent) if h.get("ok")), None),
        "last": last,
    }


def main():
    cand_path = os.path.join(DATA, "candidates.json")
    health_path = os.path.join(DATA, "health.json")
    cdoc = json.load(open(cand_path, encoding="utf-8"))
    stations = cdoc["stations"]
    stations_all = cdoc["stations"]

    health = {"stations": {}}
    if os.path.exists(health_path):
        try:
            health = json.load(open(health_path, encoding="utf-8"))
        except Exception:
            pass
    hs = health.setdefault("stations", {})

    limit = int(os.environ.get("LIMIT", "0"))
    only = os.environ.get("ONLY", "").strip()
    if only:
        stations = [s for s in stations if s["id"] in only.split(",")]
    elif limit:
        stations = stations[:limit]

    # backoff: stanice koje su mrtve 5+ puta zaredom provjeravamo rjede
    def due(st):
        h = hs.get(st["id"], {})
        streak = h.get("summary", {}).get("fail_streak", 0)
        if streak >= 10:
            return random.random() < 0.15
        if streak >= 5:
            return random.random() < 0.4
        return True

    queue = [s for s in stations if due(s)]
    print(f"Provjeravam {len(queue)} / {len(stations)} stanica, {WORKERS} paralelno")

    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(check_one, s): s for s in queue}
        for f in as_completed(futs):
            st = futs[f]
            try:
                sid, rec = f.result()
            except Exception as e:
                sid, rec = st["id"], {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                      "ok": False, "error": str(e)[:120]}
            entry = hs.setdefault(sid, {"history": []})
            entry["history"] = (entry.get("history", []) + [rec])[-HISTORY_LEN:]
            entry["summary"] = summarize(entry["history"])
            done += 1
            if done % 50 == 0:
                print(f"  ... {done}/{len(queue)}")

    health["generated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(health_path, "w", encoding="utf-8") as f:
        json.dump(health, f, ensure_ascii=False, indent=1)

    # --- Lossless je IZMJERENA cinjenica, ne tudja oznaka --------------------
    # Zanr se dosad dodjeljivao pri dohvatu, nagadjanjem iz oznaka Radio
    # Browsera. Zato je 35 stvarno lossless stanica zavrsilo pod "other", a
    # dio pod starim nazivom "audiophile". Sada odluku donosi mjerenje:
    # sto je dekoder potvrdio kao lossless, dobiva oznaku "lossless".
    changed = 0
    for st in stations_all:
        last = (hs.get(st["id"], {}).get("summary", {}) or {}).get("last") or {}
        gl = st.setdefault("genres", [])
        if "audiophile" in gl:                       # stari naziv -> novi
            gl[:] = [g for g in gl if g != "audiophile"]
            if "lossless" not in gl:
                gl.append("lossless")
            changed += 1
        if last.get("lossless"):
            if "lossless" not in gl:
                gl.append("lossless")
                changed += 1
            if "other" in gl and len(gl) > 1:        # "other" vise nije potreban
                gl[:] = [g for g in gl if g != "other"]
        elif "lossless" in gl and last.get("ok"):
            # izmjereno je da NIJE lossless -> oznaka se skida
            gl[:] = [g for g in gl if g != "lossless"]
            if not gl:
                gl.append("other")
            changed += 1

    if changed:
        cdoc["stations"] = stations_all
        cdoc["count"] = len(stations_all)
        with open(cand_path, "w", encoding="utf-8") as f:
            json.dump(cdoc, f, ensure_ascii=False, indent=1)
        print(f"Zanr 'lossless' uskladjen s mjerenjem na {changed} stanica")

    tally = {}
    for v in hs.values():
        s = v.get("summary", {}).get("status", "unknown")
        tally[s] = tally.get(s, 0) + 1
    print("\nStatus:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    lossless = sum(1 for v in hs.values() if (v.get("summary", {}).get("last") or {}).get("lossless"))
    print(f"Stvarno lossless (izmjereno, ne prijavljeno): {lossless}")


if __name__ == "__main__":
    sys.exit(main())
