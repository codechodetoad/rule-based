#!/usr/bin/env python3
"""Build cache RxCUI cho các span thuốc (query RxNav ở DEV -> drugs_rxnorm_cache.json).

Runtime của extract_drugs.py CHỈ đọc cache tĩnh này (offline). Đây là bước dev-time.
- Span có liều  -> approximateTerm (chuẩn hóa 'po bid daily' -> bỏ; '25mg' -> '25 mg').
- Tên trần      -> findRxcuiByString (ingredient) -> fallback approximateTerm.
RxNav: https://rxnav.nlm.nih.gov/REST/  (miễn phí, không key, 20 req/s)
"""
import json, os, sys, io, re, time, urllib.parse, urllib.request, glob

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CACHE = os.path.join(HERE, "drugs_rxnorm_cache.json")
BASE = "https://rxnav.nlm.nih.gov/REST"

def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None

def approximate(term):
    url = BASE + "/approximateTerm.json?" + urllib.parse.urlencode({"term": term, "maxEntries": 6})
    d = _get(url)
    if not d: return None
    cands = (d.get("approximateGroup") or {}).get("candidate") or []
    # ưu tiên nguồn RXNORM (mã chuẩn), giữ thứ hạng
    for c in cands:
        if c.get("source") == "RXNORM" and c.get("rxcui"):
            return c["rxcui"]
    return cands[0]["rxcui"] if cands and cands[0].get("rxcui") else None

def find_rxcui(name):
    url = BASE + "/rxcui.json?" + urllib.parse.urlencode({"name": name, "search": 2})
    d = _get(url)
    ids = ((d or {}).get("idGroup") or {}).get("rxnormId") or []
    return ids[0] if ids else None

def clean_for_query(span):
    s = span.lower()
    s = re.sub(r"\b(po|iv|im|sc|sl|pr|bid|tid|qid|qod|qd|qhs|qam|qpm|prn|daily|nebs?|inhaler|"
               r"puffs?|hàng ngày|mỗi ngày|đường uống|dưới lưỡi|xịt)\b", " ", s)
    s = re.sub(r":prn|x\s*\d+|q\d+h(?::prn)?|q\d+-\d+h", " ", s)
    s = re.sub(r"(\d)\s*(mg|mcg|ml|meq|iu)", r"\1 \2", s)   # 25mg -> 25 mg
    s = re.sub(r"\s+", " ", s).strip()
    return s

def resolve(span):
    q = clean_for_query(span)
    if not q: return []
    has_dose = bool(re.search(r"\d", q))
    rx = None
    if has_dose:
        rx = approximate(q)
    else:
        rx = find_rxcui(q) or approximate(q)
    return [rx] if rx else []

def main():
    # thu thập span unique từ output_drugs (đã trích), bỏ span có override (VN)
    override_names = set()
    with open(os.path.join(HERE, "lexicon", "drugs.txt"), encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#") and "|" in s:
                override_names.add(s.split("|")[0].strip().lower())
    spans = set()
    for fp in glob.glob(os.path.join(ROOT, "output_drugs", "*.json")):
        for c in json.load(open(fp, encoding="utf-8")):
            t = c["text"].lower()
            if t not in override_names:
                spans.add(t)
    spans = sorted(spans)
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    todo = [s for s in spans if s not in cache]
    print(f"Spans unique: {len(spans)} | cần query: {len(todo)}")
    for k, span in enumerate(todo, 1):
        cache[span] = resolve(span)
        if k % 20 == 0:
            print(f"  {k}/{len(todo)} ...")
        time.sleep(0.06)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    filled = sum(1 for v in cache.values() if v)
    print(f"Cache: {len(cache)} span | có RxCUI: {filled} | rỗng: {len(cache) - filled}")

if __name__ == "__main__":
    main()
