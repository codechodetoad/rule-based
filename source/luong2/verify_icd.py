#!/usr/bin/env python3
"""Verify các mã ICD-10-CM gán trong lexicon/diagnoses.txt bằng NLM Clinical Tables API.

Với mỗi dòng "vi | codes | gloss": query API theo gloss, kiểm tra mỗi code gán có nằm
trong tập code API trả về cho gloss đó không. In danh sách CẦN SOÁT (assigned code không khớp).
API: https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search  (miễn phí, không cần key)
Chỉ dùng ở DEV để soát mã; runtime của extractor KHÔNG gọi API (đọc mã tĩnh từ lexicon).
"""
import json, os, sys, io, time, urllib.parse, urllib.request

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
DIAG = os.path.join(HERE, "lexicon", "diagnoses.txt")
API = "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search"

def query(gloss, maxlist=30):
    url = API + "?" + urllib.parse.urlencode({"sf": "code,name", "terms": gloss, "maxList": maxlist})
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        codes = data[1] or []
        pairs = data[3] or []
        return codes, pairs
    except Exception as e:
        return None, str(e)

def parse_diag():
    rows = []
    with open(DIAG, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "|" not in s:
                continue
            parts = [p.strip() for p in s.split("|")]
            phrase = parts[0]
            codes = [c.strip() for c in parts[1].split(",")] if len(parts) > 1 and parts[1] else []
            gloss = parts[2] if len(parts) > 2 else phrase
            rows.append((phrase, codes, gloss))
    return rows

def fam(code):
    """Họ mã 3 ký tự: 'K21.0' -> 'K21', 'I10' -> 'I10'."""
    return code.replace(".", "")[:3]

def main():
    rows = parse_diag()
    flagged = []
    ok = 0
    for phrase, codes, gloss in rows:
        api_codes, pairs = query(gloss)
        if api_codes is None:
            print(f"  API ERROR for {gloss!r}: {pairs}")
            continue
        api_fams = {fam(c) for c in api_codes}
        missing = [c for c in codes if fam(c) not in api_fams]
        if missing:
            top = [f"{c}:{n}" for c, n in (pairs[:4] if isinstance(pairs, list) else [])]
            flagged.append((phrase, codes, missing, gloss, top))
        else:
            ok += 1
        time.sleep(0.05)
    print(f"Verified: {len(rows)} diagnoses | OK: {ok} | CẦN SOÁT: {len(flagged)}\n")
    for phrase, codes, missing, gloss, top in flagged:
        print(f"[SOÁT] {phrase!r}")
        print(f"       gán = {codes} ; không khớp API = {missing}")
        print(f"       gloss = {gloss!r}")
        print(f"       API top = {top}")
    if not flagged:
        print("ALL ICD CODES VERIFIED ✅")

if __name__ == "__main__":
    main()
