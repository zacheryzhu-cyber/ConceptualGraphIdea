"""Make two extra test sheets from the DEXPI sample by renumbering tags and line numbers.
Sheet B: continues from sheet A's outlet line 47126 and shares heat exchanger H1008 with A.
Sheet C: an unrelated system (no shared tags, no matching lines) -> an island."""
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()

def variant(line_map, tag_map):
    def fix(m):
        v = m.group(1)
        if v in line_map:
            return f'Value="{line_map[v]}"'
        for old, new in tag_map.items():
            v = v.replace(old, new)
        return f'Value="{v}"'
    return re.sub(r'Value="([^"]*)"', fix, src)

lines = [f"4712{i}" for i in range(1, 8)]
b = variant({**{l: "5" + l[1:] for l in lines}, "47121": "47126"},
            {"4711": "5711", "4712": "5712", "4750": "5750", "1007": "2007"})   # 1008 kept -> shared
c = variant({l: "6" + l[1:] for l in lines},
            {"4711": "6711", "4712": "6712", "4750": "6750", "1007": "3007", "1008": "3008"})
open("test_sheets/sheet_B.xml", "w", encoding="utf-8").write(b)
open("test_sheets/sheet_C.xml", "w", encoding="utf-8").write(c)
