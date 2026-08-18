# RadioTree Stream Manager

Kurirana baza stanica za RadioTree Android TV aplikaciju.
Bez servera, bez baze podataka, bez mjesecnog racuna. GitHub Actions radi posao, GitHub Pages servira rezultat.

## Kako radi

```
GitHub Actions (dnevno)
  fetch_candidates.py   Radio Browser -> data/candidates.json      (stroj)
  check.py              ffprobe + ICY -> data/health.json          (stroj)
  build_db.py           + data/decisions.json  -> db/v1/           (TI + stroj)
                                                     |
                        radiotree.app/db/v1/manifest.json  <- TV app cita ovo
```

Kljucno pravilo: **stroj mjeri, ti presudjujes.** `decisions.json` je jedina datoteka
koju uredjujes ti i nijedan skript je ne prepisuje.

## Jedan workflow, zadatak se bira iz padajuceg izbornika

Actions -> **RadioTree DB** -> Run workflow -> `task`:

| task | sto radi |
|---|---|
| `bootstrap` | ispise projekt u repo (pokrenuto jednom, gotovo) |
| `fetch` | povuce nove kandidate iz Radio Browsera |
| `check` | izmjeri sve streamove ffprobeom |
| `import` | uveze tvoje liste iz `data/import/` |
| `autopick` | odabere najboljih N po izmjerenim kriterijima |
| `build` | napravi `db/v1/stations.json` + `.csv` |

Automatski svaki dan u 03:17 UTC: `check` + `build`.

## Prvo pokretanje

1. task = `fetch`   (~20 min, skuplja i mjeri)
2. task = `autopick`, total = 500
3. Settings -> Pages -> Deploy from branch -> `main` / root
4. `https://<user>.github.io/<repo>/admin/` -> slusaj, 1 = NE, 2 = MOZDA, 3 = DA
5. Tab SPREMI -> kopiraj -> zalijepi u `data/decisions.json` -> commit
6. task = `build`

## Sto TV aplikacija cita

`https://radiotree.app/db/v1/manifest.json`

```json
{"version": 12, "sha256": "...", "count": 412, "stations_url": "stations.json"}
```

App uspoređuje `version`/`sha256` sa spremljenim. Ako je isto, ne skida nista.
Ako se razlikuje, skine `stations.json` i zamijeni lokalni cache.

**Nikad ne hardkodiraj github.io URL u aplikaciju.** Uvijek vlastita domena.

## Sto mjerimo, a Radio Browser ne zna

| polje | izvor | pouzdanost |
|---|---|---|
| `rb_codec`, `rb_bitrate` | Radio Browser | cesto lazu |
| `codec`, `bitrate`, `sample_rate`, `lossless` | ffprobe na stvarnom streamu | ground truth |
| `connect_ms` | mjereno | ground truth |
| `uptime` | rolling 30 provjera | ground truth |

Admin alat ti crveno oznaci stanicu gdje se deklarirano i izmjereno razlikuju >25%.
To je tocno ono sto ti treba za posten Audiophile tab.

## Backoff

Stanice koje padnu 5x zaredom provjeravaju se u 40% slucajeva, 10x zaredom u 15%.
Ne gadjamo tudje servere bez razloga i ne riskiramo ban.
