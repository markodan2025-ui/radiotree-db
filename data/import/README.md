# data/import/

Ovdje ubacujes SVOJE liste stanica. Sve datoteke u ovom folderu se procitaju.
Format se prepoznaje sam.

## 1. Samo URL-ovi, jedan po retku
```
http://stream.example.com/live
https://drugi.example.com/flac
```

## 2. Ime | URL | zanrovi   (preporuceno)
```
Miles Davis Radio | http://stream.example.com/live | jazz,artist
Pink Floyd Radio  | http://drugi.example.com/mp3   | rock,artist
```

## 3. JSON
```json
[{"name":"Miles Davis Radio","url":"http://...","genres":["jazz","artist"]}]
```

## 4. Zalijepljen JS/HTML blok iz RadioTree aplikacije
Izvuce URL-ove i najbliza imena regexom. Radi, ali provjeri rezultat u KATALOGU.

Retci koji pocinju s `#` ili `//` se ignoriraju.

Nakon commita pokreni Actions -> "Import manual list".
