"""Build daemon/lantern/data/places.tsv.gz from GeoNames (CC-BY 4.0, https://www.geonames.org): every city with at
least 15 000 inhabitants (not its districts), its coordinates and country, one display name per language and every
other name it is known by (for matching place words in queries in any language).

A development step, run by hand when the table should be refreshed; Lantern itself never downloads anything:

    curl -O https://download.geonames.org/export/dump/cities15000.zip
    curl -O https://download.geonames.org/export/dump/alternateNamesV2.zip
    unzip cities15000.zip && unzip alternateNamesV2.zip alternateNamesV2.txt
    uv run python scripts/build_places.py <folder with both .txt files>

Output columns (tab-separated, one city per line): geonameid, latitude, longitude, country code, name,
display names ("lang=name" joined by "|"), other names (joined by "|").
"""

import gzip
import sys
from pathlib import Path

# alternateNamesV2 "languages" that are not languages: links, codes, transliteration schemes
NOT_LANGUAGES = {"link", "wkdt", "post", "iata", "icao", "faac", "abbr", "unlc", "fr_1793", "phon", "piny", "tcid"}
# Parts of a city ("Paris 03 Temple", Warsaw's districts) and abandoned or historical places: a photo is labelled with
# the city itself.
SKIP_FEATURES = {"PPLX", "PPLH", "PPLQ", "PPLW"}
OUT = Path(__file__).resolve().parent.parent / "daemon" / "lantern" / "data" / "places.tsv.gz"


def main(src: Path) -> None:
    cities: dict[int, list] = {}
    with open(src / "cities15000.txt", encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            # id, name, asciiname, alternatenames, lat, lon, feature class, feature code (7), country code (8)
            if c[7] in SKIP_FEATURES:
                continue
            cities[int(c[0])] = [round(float(c[4]), 4), round(float(c[5]), 4), c[8], c[1], {}, {c[1], c[2]}]
    with open(src / "alternateNamesV2.txt", encoding="utf-8") as f:
        for line in f:
            a = line.rstrip("\n").split("\t")
            city = cities.get(int(a[1]))
            if city is None:
                continue
            lang, name, preferred, short, colloquial, historic = a[2], a[3], a[4], a[5], a[6], a[7]
            if lang in NOT_LANGUAGES or historic == "1" or colloquial == "1" or "|" in name or "=" in name:
                continue
            city[5].add(name)
            if lang:
                rank = 0 if preferred == "1" else 2 if short == "1" else 1
                if lang not in city[4] or rank < city[4][lang][0]:
                    city[4][lang] = (rank, name)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as out:
        for gid in sorted(cities):
            lat, lon, cc, name, display, names = cities[gid]
            shown = "|".join(f"{lang}={n}" for lang, (_, n) in sorted(display.items()) if n != name)
            other = "|".join(sorted(n for n in names if n and n != name and n not in {d[1] for d in display.values()}))
            out.write(f"{gid}\t{lat}\t{lon}\t{cc}\t{name}\t{shown}\t{other}\n")
    print(f"{len(cities)} cities -> {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
