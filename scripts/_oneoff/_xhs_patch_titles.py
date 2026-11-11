"""纯 Python 把 16 个 case 的 out/xhs/<id>/note.json 标题改成干净标题。

只动 title 字段，保留 body/tags/images（卡片图不依赖标题，且从未被标违规）。
金额红线违规只出现在 note 的标题文本字段，改它即可，不必重建卡片图、不必起 9333。
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from xhs_publish import make_xhs_title

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CASES = ['augora-ai', 'boothie-ai-photo-editor', 'downconvert-sketchup-files',
         'fastwrite-io', 'growingupvideo', 'homy', 'macwall', 'nutria-9e3eaa',
         'pet-plus-us', 'publbee', 'quotedeck', 'stealth-rdp', 'vectosolve',
         'wagiff-app', 'white-glove-content',
         'wpconvert-ai-convert-ai-sites-to-wordpress']

# 加载 cases.json 取案例对象
cj = json.load(open(os.path.join(ROOT, 'data', 'cases.json'), encoding='utf-8'))
items = cj if isinstance(cj, list) else cj.get('cases', cj)
by_id = {c.get('id'): c for c in items}

MONEY = __import__('re').compile(
    r'月收\s*[¥€£$]?\d|月入\s*\d|\$[0-9]|€\d|£\d|\d\s*欧|\d\s*美元|\d\s*刀|赚\s*\d|MRR')

changed = 0
for cid in CASES:
    c = by_id.get(cid)
    if not c:
        print("  [skip] cases.json 无 %s" % cid)
        continue
    p = os.path.join(ROOT, 'out', 'xhs', cid, 'note.json')
    if not os.path.exists(p):
        print("  [skip] 无 note.json: %s" % cid)
        continue
    d = json.load(open(p, encoding='utf-8'))
    old = d.get('title', '')
    new = make_xhs_title(c)
    d['title'] = new
    json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    flag = 'OK' if not MONEY.search(new) else '!!金额仍违规'
    print("  [%s] %s\n     旧: %s\n     新: %s" % (flag, cid, old, new))
    changed += 1

print("\n共改写 %d 个 note.json" % changed)
