"""本地校验：对 16 个要重发的 case，build_note 产出的标题是否干净（无金额红线）。
不连浏览器。"""
import sys, os, re, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xhs_publish as xp

CASES = ['augora-ai', 'boothie-ai-photo-editor', 'downconvert-sketchup-files',
         'fastwrite-io', 'growingupvideo', 'homy', 'macwall', 'nutria-9e3eaa',
         'pet-plus-us', 'publbee', 'quotedeck', 'stealth-rdp', 'vectosolve',
         'wagiff-app', 'white-glove-content',
         'wpconvert-ai-convert-ai-sites-to-wordpress']

ALL = json.load(open(os.path.join(ROOT, "data", "cases.json"), encoding="utf-8"))
by_id = {c["id"]: c for c in ALL}

MONEY = re.compile(
    r'月收\s*[¥€£$]?\d|月入\s*\d|\$[0-9]|€\d|£\d|\d\s*欧|\d\s*美元|\d\s*刀|赚\s*\d'
    r'|MRR|ARR|\d[\d,]*\s*万美')

bad = 0
for cid in CASES:
    c = by_id.get(cid)
    if not c:
        print("[缺失] %s 不在 cases.json" % cid)
        bad += 1
        continue
    note = xp.build_note(c)
    title = note["title"]
    flag = "✓" if not MONEY.search(title) else "✗金额"
    if MONEY.search(title):
        bad += 1
    print("[%s] %-42s → %s" % (flag, cid, title))

print("\n违规标题数：%d / %d" % (bad, len(CASES)))
