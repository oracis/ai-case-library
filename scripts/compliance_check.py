"""四平台内容合规自查器。

用法：
    python scripts/compliance_check.py <待发布内容.md>
    python scripts/compliance_check.py --stdin          # 从标准输入读
    python scripts/compliance_check.py <文件> --platform 小红书

设计原则：**只做语义级提示，不做关键词判罪**。
红线是语义级的（暗示可联系也能被判导流），所以脚本只能给
「需要人工确认」的方向，命中不等于违规，不命中也不等于安全。

退出码：
    0 = 无高危信号
    1 = 命中高危信号，需人工复核
    2 = 用法错误
"""
import argparse
import re
import sys

# ── 禁售类目 → (小红书, 公众号, 头条, B站) ────────────────────────────
# 符号：❌ 禁售 / ⚠ 需资质 / ✅ 可发
BANNED_CATEGORIES = [
    # (类目关键词, 四平台判定, 说明)
    (["虚拟号", "虚拟号码", "物联卡", "sim卡", "接码", "外呼", "短信通道", "语音专线"],
     ("禁售", "需资质", "需资质", "需资质"),
     "通信资源类目在小红书属明确禁售，正确处置是换平台，不是改措辞"),
    (["账号租售", "租号", "代实名", "实名认证代"],
     ("禁售", "禁售", "禁售", "禁售"),
     "账号租售/代实名四平台全禁，无处置空间"),
    (["破解", "外挂", "刷量", "刷粉", "越狱", "盗版"],
     ("禁售", "禁售", "禁售", "禁售"),
     "破解/外挂/刷量四平台全禁"),
    (["灰产", "黑产", "接码平台", "卡商"],
     ("禁售", "需资质", "需资质", "需资质"),
     "灰产工具教学在各平台均受限"),
]

# ── 导流信号（语义级，命中只提示不判罪）────────────────────────────
TRAFFIC_PATTERNS = [
    (r"(微信|weixin|wx)\s*[:：]?\s*[A-Za-z0-9_-]{5,}", "疑似微信号"),
    (r"(qq|扣扣)\s*[:：]?\s*[0-9]{5,}", "疑似QQ号"),
    (r"加\s*(群|好友|微)", "疑似引导加群/加好友"),
    (r"(评论区|私信|留言区)\s*(聊聊|交流|扣|滴|私聊)", "评论区引导互动"),
    (r"(找我|私我|联系我|dd我)\s*[:：]?", "直接引导联系"),
]

# ⚠ 不作为导流判据的上下文 —— 这些是技术文章里的正常用法
TRAFFIC_SAFE_CONTEXT = [
    r"api[_-]?key", r"token", r"webhook", r"chat_id", r"bot[_-]?id",
    r"getAll\(\)", r"IndexedDB", r"user-agent", r"cookies?",
]

# ── 非正规工具 / 绕过验证 ──────────────────────────────────────────
BYPASS_PATTERNS = [
    (r"(绕过|规避|跳过|破解)\s*(验证|风控|审核|限制|检测)", "教人绕过平台验证/风控"),
    (r"(免实名|无需实名|不需要实名)(?!.{0,6}要求)", "宣传免实名"),
    (r"(自建|搭建).{0,10}(接码|验证码平台|短信平台)", "自建接码/验证码服务"),
]

# ── 正文产物互串（栏目残留）─────────────────────────────────────────
CROSS_PLATFORM_LEAK = [
    (r"公众号后台", "疑似公众号后台话术"),
    (r"小红书草稿箱", "疑似小红书后台话术"),
    (r"抖音.{0,6}作品", "疑似抖音话术"),
    (r"B站.{0,6}三连|一键三连", "疑似B站话术（若非目标平台）"),
]

PLATFORMS = ["小红书", "公众号", "今日头条", "B站"]


def strip_code_and_style(text):
    """剥掉代码块与 style 标签 —— 技术内容里的字符串不是文案。"""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def hit_context(text, pattern, span=40):
    """命中处上下文 —— 让人能自己判断是不是误报。"""
    out = []
    for m in re.finditer(pattern, text, re.I):
        s = max(0, m.start() - span)
        e = min(len(text), m.end() + span)
        out.append(re.sub(r"\s+", " ", text[s:e]).strip())
    return out


def check(text, target_platform=None):
    """返回 (高危项, 提示项)。高危 = 明确禁售类目命中。"""
    body = strip_code_and_style(text)
    fatal, warns = [], []

    # 1) 禁售类目
    for keywords, verdicts, note in BANNED_CATEGORIES:
        found = [k for k in keywords if k in body]
        if not found:
            continue
        v = verdicts
        entry = {
            "cat": "、".join(found),
            "note": note,
            "verdicts": dict(zip(PLATFORMS, v)),
        }
        # 目标平台若明确禁售 → 高危
        if target_platform and v[PLATFORMS.index(target_platform)] == "禁售":
            entry["level"] = "fatal"
            fatal.append(entry)
        else:
            entry["level"] = "warn"
            warns.append(entry)

    # 2) 导流信号（语义级，只提示）
    for pat, label in TRAFFIC_PATTERNS:
        ctx = hit_context(body, pat)
        if not ctx:
            continue
        # 若整段都处在技术上下文里，降级为「疑似」
        safe = any(re.search(s, body, re.I) for s in TRAFFIC_SAFE_CONTEXT)
        warns.append({
            "level": "info",
            "cat": label,
            "note": "语义级判据 —— 命中不等于违规，但「暗示可联系」同样会被判导流",
            "context": ctx[:2],
            "likely_false_positive": safe,
        })

    # 3) 绕过验证
    for pat, label in BYPASS_PATTERNS:
        if re.search(pat, body, re.I):
            warns.append({
                "level": "warn",
                "cat": label,
                "note": "提供非正规工具/绕过验证是明确高危判定项",
            })

    # 4) 栏目残留
    for pat, label in CROSS_PLATFORM_LEAK:
        if re.search(pat, body, re.I):
            warns.append({
                "level": "warn",
                "cat": label,
                "note": "跨平台发布前须确认这段是否该保留",
            })

    return fatal, warns


def main():
    ap = argparse.ArgumentParser(description="四平台内容合规自查器")
    ap.add_argument("path", nargs="?", help="待检查的markdown / 文本文件")
    ap.add_argument("--stdin", action="store_true", help="从标准输入读取")
    ap.add_argument("--platform", choices=PLATFORMS, help="目标平台（给出后禁售类目命中会升为高危）")
    args = ap.parse_args()

    if args.stdin:
        text = sys.stdin.read()
    elif args.path:
        with open(args.path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        print(__doc__)
        return 2

    fatal, warns = check(text, args.platform)

    print("=" * 60)
    if args.platform:
        print("目标平台：{}".format(args.platform))
    else:
        print("目标平台：未指定（禁售类目不会升为高危，建议用 --platform 指定）")
    print("=" * 60)

    if not fatal and not warns:
        print("\n✅ 未命中任何信号。\n注意：无信号 ≠ 安全，红线是语义级的，仍需人工判断。")
        return 0

    if fatal:
        print("\n🔴 高危（命中明确禁售类目）")
        for w in fatal:
            print("  【{}】{}".format(w["cat"], w["note"]))
            print("     四平台判定：{}".format(
                " / ".join("{}={}".format(k, v) for k, v in w["verdicts"].items())))
            if not args.platform:
                print("     ⚠ 未指定平台：请逐个平台确认后再发")
        print("\n  处置：换平台 > 改整段意图。替换个别词无效。")

    if warns:
        print("\n🟡 需人工复核")
        for w in warns:
            print("  [{}] {}".format(w["level"], w["cat"]))
            if w.get("note"):
                print("      {}".format(w["note"]))
            for c in w.get("context", []):
                print("      …{}…".format(c[:70]))
            if w.get("likely_false_positive"):
                print("      （上下文含技术术语，可能是误报）")

    print()
    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())