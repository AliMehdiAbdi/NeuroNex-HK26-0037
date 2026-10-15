"""Geocodes and catalogs documented ground-truth flood observations in Lucknow."""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT_DIR = os.path.join(PROJECT_ROOT, "backend", "data", "spatial", "pilot")
CACHE = os.path.join(HERE, "geocode_cache.json")
GRAPH = os.path.join(OUT_DIR, "drainage_graph.graphml")

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "DrainIQ/1.0 (urban emergency management; drainage graph validation)"
SLEEP_S = 1.1

SITE_RADIUS_M = 400.0

OBSERVATIONS = [
    # Amar Ujala, 25 Aug 2026
    dict(
        name="Charbagh Railway Station",
        expect="landmark",
        query="Charbagh Railway Station, Lucknow",
        severity="chronic",
        source_url="https://www.amarujala.com/lucknow/instructions-to-improve-the-drainage-system-at-charbagh-station-lucknow-news-c-13-lko1070-1889244-2026-08-25",
        source_date="2026-08-25",
        quote="चारबाग रेलवे स्टेशन पर बारिश में जलभराव की समस्या पुरानी है (waterlogging at Charbagh station during rain is a longstanding problem)",
    ),
    # Times of India, 18 Aug 2026
    dict(
        name="Jankipuram",
        expect="area",
        query="Jankipuram, Lucknow",
        severity="severe",
        source_url="https://timesofindia.indiatimes.com/city/lucknow/intermittent-rain-disrupts-city-for-second-day-key-areas-hit-by-waterlogging/articleshow/133305898.cms",
        source_date="2026-08-18",
        quote="Roads in Jankipuram and Faizullaganj recorded water levels reaching up to three feet",
    ),
    dict(
        name="Faizullaganj",
        expect="area",
        query="Faizullaganj, Lucknow",
        severity="severe",
        source_url="https://timesofindia.indiatimes.com/city/lucknow/intermittent-rain-disrupts-city-for-second-day-key-areas-hit-by-waterlogging/articleshow/133305898.cms",
        source_date="2026-08-18",
        quote="Roads in Jankipuram and Faizullaganj recorded water levels reaching up to three feet",
    ),
    dict(
        name="Gomti Nagar",
        expect="area",
        query="Gomti Nagar, Lucknow",
        severity="reported",
        source_url="https://timesofindia.indiatimes.com/city/lucknow/intermittent-rain-disrupts-city-for-second-day-key-areas-hit-by-waterlogging/articleshow/133305898.cms",
        source_date="2026-08-18",
        quote="waterlogging also affected Gomti Nagar, Aliganj, Niralanagar and Thakurganj",
    ),
    dict(
        name="Aliganj",
        expect="area",
        query="Aliganj, Lucknow",
        severity="reported",
        source_url="https://timesofindia.indiatimes.com/city/lucknow/intermittent-rain-disrupts-city-for-second-day-key-areas-hit-by-waterlogging/articleshow/133305898.cms",
        source_date="2026-08-18",
        quote="waterlogging also affected Gomti Nagar, Aliganj, Niralanagar and Thakurganj",
    ),
    dict(
        name="Nirala Nagar",
        expect="area",
        query="Nirala Nagar, Lucknow",
        severity="reported",
        source_url="https://timesofindia.indiatimes.com/city/lucknow/intermittent-rain-disrupts-city-for-second-day-key-areas-hit-by-waterlogging/articleshow/133305898.cms",
        source_date="2026-08-18",
        quote="waterlogging also affected Gomti Nagar, Aliganj, Niralanagar and Thakurganj",
    ),
    dict(
        name="Thakurganj",
        expect="area",
        query="Thakurganj, Lucknow",
        severity="reported",
        source_url="https://timesofindia.indiatimes.com/city/lucknow/intermittent-rain-disrupts-city-for-second-day-key-areas-hit-by-waterlogging/articleshow/133305898.cms",
        source_date="2026-08-18",
        quote="waterlogging also affected Gomti Nagar, Aliganj, Niralanagar and Thakurganj",
    ),
    dict(
        name="Vikas Nagar",
        expect="area",
        query="Vikas Nagar, Lucknow",
        severity="severe",
        source_url="https://timesofindia.indiatimes.com/city/lucknow/intermittent-rain-disrupts-city-for-second-day-key-areas-hit-by-waterlogging/articleshow/133305898.cms",
        source_date="2026-08-18",
        quote="The situation worsened in parts of Vikas Nagar after a portion of a road caved in",
    ),
    dict(
        name="Rumi Darwaza",
        expect="landmark",
        query="Rumi Darwaza, Lucknow",
        severity="reported",
        source_url="https://timesofindia.indiatimes.com/city/lucknow/intermittent-rain-disrupts-city-for-second-day-key-areas-hit-by-waterlogging/articleshow/133305898.cms",
        source_date="2026-08-18",
        quote="Commuters wade through the waterlogged road at the Rumi Gate on Monday",
    ),
    dict(
        name="Hussainganj",
        expect="area",
        query="Hussainganj, Lucknow",
        severity="traffic",
        source_url="https://timesofindia.indiatimes.com/city/lucknow/intermittent-rain-disrupts-city-for-second-day-key-areas-hit-by-waterlogging/articleshow/133305898.cms",
        source_date="2026-08-18",
        quote="Traffic congestion was reported around key areas, including Vidhan Sabha, Hazratganj, Hussainganj, Tedhi Puliya, Kursi Road, Aliganj and Purania",
    ),
    dict(
        name="Hazratganj",
        expect="area",
        query="Hazratganj, Lucknow",
        severity="traffic",
        source_url="https://timesofindia.indiatimes.com/city/lucknow/intermittent-rain-disrupts-city-for-second-day-key-areas-hit-by-waterlogging/articleshow/133305898.cms",
        source_date="2026-08-18",
        quote="Traffic congestion was reported around key areas, including Vidhan Sabha, Hazratganj, Hussainganj, Tedhi Puliya, Kursi Road, Aliganj and Purania",
    ),
    # Newstrack, 17 Aug 2026
    dict(
        name="Ashiyana",
        expect="area",
        query="Ashiyana, Lucknow",
        severity="severe",
        source_url="https://newstrack.com/uttar-pradesh/lucknow/lucknow-heavy-rain-waterlogging-ashiyana-sector-m-sector-h-drainage-system-623703",
        source_date="2026-08-17",
        quote="आशियाना के सेक्टर-M और सेक्टर-H में जलभराव से लोगों की मुश्किलें बढ़ीं (waterlogging in Ashiyana Sector M and Sector H)",
    ),
    # Dainik Jagran, 16 Aug 2026
    dict(
        name="Jankipuram Colonies",
        expect="area",
        query="Jankipuram Vistar, Lucknow",
        severity="severe",
        source_url="https://www.jagran.com/uttar-pradesh/lucknow-city-waterlogging-in-many-places-in-lucknow-after-rain-public-facing-problems-40342450.html",
        source_date="2026-08-16",
        quote="कॉलोनियों में दो से तीन फीट तक पानी… सबसे ज्यादा असर जानकीपुरम में (two to three feet of water in colonies; Jankipuram worst affected)",
    ),
    # Hindustan Times, 7 Jul 2024
    dict(
        name="Kalyanpur",
        expect="area",
        query="Kalyanpur, Lucknow",
        severity="reported",
        source_url="https://www.hindustantimes.com/cities/lucknow-news/downpour-exposes-lucknow-s-waterlogging-preparedness-101720379819187.html",
        source_date="2024-07-07",
        quote="Other areas like Kalyanpur and Ashiana also affected",
    ),
]

STRONG = {"severe", "chronic", "reported"}

AREA_TYPES = {
    "suburb",
    "neighbourhood",
    "quarter",
    "residential",
    "city_district",
    "town",
    "village",
    "hamlet",
    "locality",
    "administrative",
}
LANDMARK_TYPES = {
    "station",
    "halt",
    "railway",
    "gate",
    "attraction",
    "monument",
    "historic",
    "tourism",
    "memorial",
    "bus_station",
}


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def geocode(query, expect, cache):
    """Geocode query via Nominatim with spatial type validation."""
    key = f"{query}|{expect}"
    if key in cache:
        return cache[key]
    url = f"{NOMINATIM}?{urllib.parse.urlencode({'q': query, 'format': 'json', 'limit': 5})}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as exc:
        print(f"    ! geocode failed for {query!r}: {exc}")
        cache[key] = None
        return None
    time.sleep(SLEEP_S)
    if not data:
        print(f"    ! NO OSM MATCH for {query!r} -> excluded")
        cache[key] = None
        return None

    allowed = AREA_TYPES if expect == "area" else (LANDMARK_TYPES | AREA_TYPES)
    good = [
        h
        for h in data
        if h.get("type") in allowed
        or h.get("addresstype") in allowed
        or h.get("class") in allowed
    ]
    if not good:
        top = data[0]
        print(
            f"    ! WRONG TYPE for {query!r}: best hit is {top.get('type')!r} ({top.get('display_name', '')[:52]})"
        )
        cache[key] = None
        return None

    hit = good[0]
    cache[key] = dict(
        lon=float(hit["lon"]),
        lat=float(hit["lat"]),
        osm_name=hit.get("display_name", ""),
        osm_type=hit.get("type", ""),
        demoted=bool(hit is not data[0]),
    )
    return cache[key]


def main():
    print("Building ground-truth flood sites database...")
    cache = load_cache()
    sites = []
    for o in OBSERVATIONS:
        got = geocode(o["query"], o["expect"], cache)
        if not got:
            continue
        sites.append(dict(o, **got))

    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=1, ensure_ascii=False)

    if not os.path.exists(GRAPH):
        print("Graph not found at", GRAPH)
        return 0

    import networkx as nx
    from pyproj import Transformer

    G = nx.read_graphml(GRAPH)
    ids = list(G.nodes)
    nlon = np.array([G.nodes[i]["lon"] for i in ids])
    nlat = np.array([G.nodes[i]["lat"] for i in ids])
    nx_ = np.array([G.nodes[i]["x"] for i in ids])
    ny_ = np.array([G.nodes[i]["y"] for i in ids])
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)

    bbox = (nlon.min(), nlat.min(), nlon.max(), nlat.max())

    for s in sites:
        sx, sy = to_utm.transform(s["lon"], s["lat"])
        cnt = int((np.hypot(nx_ - sx, ny_ - sy) <= SITE_RADIUS_M).sum())
        inside = (bbox[0] <= s["lon"] <= bbox[2]) and (bbox[1] <= s["lat"] <= bbox[3])
        s["manholes_within_radius"] = cnt
        s["in_zone"] = bool(inside and cnt > 0)
        s["is_strong"] = s["severity"] in STRONG

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "flood_sites.json"), "w", encoding="utf-8") as f:
        json.dump(
            dict(site_radius_m=SITE_RADIUS_M, pilot_bbox=list(bbox), sites=sites),
            f,
            indent=1,
            ensure_ascii=False,
        )

    print(f"Processed {len(sites)} sites -> {OUT_DIR}/flood_sites.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
