"""
gARB — Global Places Database Generator
Generates 100K+ city/town database from geonamescache + procedural expansion.

Run: python scripts/generate_mock.py
Requires: pip install geonamescache
"""

import json, random, math
from pathlib import Path

def generate_places(output_dir="mock_data", seed=42):
    try:
        import geonamescache
        gc = geonamescache.GeonamesCache()
        cities = gc.get_cities()
        city_list = []
        for gid, c in cities.items():
            if c['population'] < 100:
                continue
            city_list.append({
                'n': c['name'], 'c': c['countrycode'],
                'p': c['population'],
                'la': round(c['latitude'], 4), 'lo': round(c['longitude'], 4),
            })
        print(f"Loaded {len(city_list)} real cities from geonamescache")
    except ImportError:
        print("geonamescache not installed — checking for existing places.json")
        existing = Path(output_dir) / "places.json"
        if existing.exists():
            data = json.load(open(existing))
            print(f"Using existing places.json ({len(data)} places)")
            return
        print("ERROR: Install geonamescache: pip install geonamescache")
        return

    # Expand with procedural towns
    random.seed(seed)
    towns = []
    for c in city_list:
        pop = c['p']
        if pop > 500000: n = random.randint(5, 10)
        elif pop > 100000: n = random.randint(3, 6)
        elif pop > 20000: n = random.randint(1, 3)
        elif pop > 5000: n = random.randint(0, 2)
        else: n = 0

        suffixes = ['Township','Village','Heights','Park','Valley','Hills','Springs',
                    'Junction','Crossing','Grove','Landing','Point','Station','Mills','Falls']
        dirs = ['North','South','East','West','Upper','Lower','New','Old']

        for i in range(n):
            offset_km = random.uniform(3, 50)
            angle = random.uniform(0, 2 * math.pi)
            dlat = offset_km / 111.32
            dlon = offset_km / (111.32 * max(0.1, math.cos(math.radians(c['la']))))
            name = f"{c['n']} {random.choice(suffixes)}" if random.random() < 0.5 else f"{random.choice(dirs)} {c['n']}"
            towns.append({
                'n': name, 'c': c['c'],
                'p': max(200, pop // random.randint(5, 100)),
                'la': round(c['la'] + dlat * math.sin(angle), 4),
                'lo': round(c['lo'] + dlon * math.cos(angle), 4),
            })

    all_places = city_list + towns
    all_places.sort(key=lambda x: x['p'], reverse=True)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "places.json", 'w') as f:
        json.dump(all_places, f, separators=(',', ':'))

    cc = len(set(p['c'] for p in all_places))
    print(f"\n{'='*50}")
    print(f"  {len(all_places):,} places across {cc} countries")
    print(f"  {len(city_list):,} real cities + {len(towns):,} generated towns")
    print(f"  Output: {out/'places.json'}")
    print(f"{'='*50}")

if __name__ == "__main__":
    generate_places()
