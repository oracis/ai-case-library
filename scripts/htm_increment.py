# -*- coding: utf-8 -*-
"""howtomakemi 增量管线一键入口：抓取 → 解析 → 对比 → 种子 → 采集 → 报告。

把 2026-10-22 / 10-23 两次手工跑通的流程固化成一个入口。此前步骤散落在
tmp/htm_screen/ 下的临时脚本里，换机器或隔几周就跑不稳；判据（索引持平
怎么办、过滤器 _blob 格式、种子与上轮重叠度）只存在于人的记忆里。

流程（默认全跑）：
  1. 抓 howtomakemi.com 首页（data-cards 属性内嵌全量索引，一次请求拿全）
  2. 解析索引 + 与库内五表对比（cases/candidates/inbox/inbox_archive/graveyard）
  3. 按类型生成种子清单：收入案例 + 开源变现 + 插件；国内实践仅登记
  4. 种子喂 TrustMRR sitemap 通道：harvest.py --source trustmrr_sitemap --tm-seeds
  5. 产出当日简报 + 索引快照 + 独有线索台账

内建判据（来自 10-22/10-23 实战）：
  · 索引持平检测：与 out 目录里上一次快照比总数，持平就明确提示
    「站点零新增，增量在遗留处置里」，并仍继续跑（harvest 的种子模式会
    从档案池重新选拔，10-23 正是这么救回 14 条被过滤器误杀的）
  · 种子重叠度检测：与 out 目录里上一轮种子清单比，100% 重叠 = 站点无新 slug
  · 已核验 slug 剔除：读 verify_results.json（如存在），核验登记过的不重跑
  · 站点无新 slug 且 harvest 无斩获 → 退出码 0（不是错误，是没新数据）

用法：
    python scripts/htm_increment.py                  # 全流程
    python scripts/htm_increment.py --dry-run        # 只解析+diff+报告，不采集
    python scripts/htm_increment.py --skip-fetch     # 复用已抓的 home_<date>.html
    python scripts/htm_increment.py --date 2026-10-23
    python scripts/htm_increment.py --out data/htm   # 台账目录（默认 tmp/htm_screen）
    python scripts/htm_increment.py --verified tmp/htm_screen/verify_results.json

退出码：0 = 正常（含「无新增」）；1 = 抓取/解析失败；2 = 采集子进程失败。
"""

import argparse
import html as H
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import text_clean as TC  # noqa: E402

HOME_URL = "https://howtomakemi.com/"
UA = "Mozilla/5.0 (compatible; CaseLibraryHarvester/1.0)"

# 入库类型的种子口径（10-14 定、10-22/23 沿用）：
#   收入案例 / 开源变现 / 插件 → 生成种子喂 TrustMRR 通道
#   国内实践 → 仅登记（超本库 thesis 范围）
SEED_TYPES = ("收入案例", "开源变现", "插件")
REGISTRY_TYPES = ("国内实践",)

# 与库内对比的五张表（graveyard 也算「已在库」，墓碑条目不重复采）
LIB_TABLES = ("cases", "candidates", "inbox", "inbox_archive", "graveyard")

MONEY_RE = re.compile(r"(¥|\$|€|£)\s?[\d,]+")


# ---------------------------------------------------------------- 基础工具

_PY_CACHE = []


def _python_ok(path):
    """候选解释器实跑一次才算数。

    坑：Windows 的 App Execution Alias（...\\WindowsApps\\python.exe）是个
    Redirector 存根，直跑只打印「Python was not found; run without arguments
    to install from the Microsoft Store」并以 9009 退出。系统 PATH 里它常排在
    真解释器前面，所以不能只看 shutil.which 的第一个命中，更不能无脑信
    sys.executable —— 必须让它 echo 一句再放行。
    """
    if not path or not os.path.isfile(path):
        return False
    if "windowsapps" in path.lower():
        return False
    try:
        proc = subprocess.run([path, "-c", "import sys"],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,
                              timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _python_exe():
    """跑 harvest 子进程用的 python（实跑验证过的绝对路径）。

    PyInstaller 打包后 sys.executable 是 exe 自己，直接拿来 subprocess 会把
    GUI 再拉起来；于是退回 PATH 上的 python / py。全都不通就退回 base_prefix。
    """
    if _PY_CACHE:
        return _PY_CACHE[0]

    import shutil
    cands = []
    if not getattr(sys, "frozen", False):
        cands.append(sys.executable)
    cands += [shutil.which(c) for c in ("python", "python3", "py")]
    cands += [os.path.join(sys.base_prefix, "python.exe"),
              os.path.join(sys.prefix, "python.exe"),
              r"C:\Python313\python.exe",
              r"C:\Python312\python.exe"]
    for p in cands:
        if _python_ok(p):
            _PY_CACHE.append(p)
            return p
    return sys.executable


def log(msg=""):
    print(msg, flush=True)


def fetch_home(dest, timeout=90):
    """抓首页 HTML。data-cards 属性在 1.9MB 的 HTML 里，timeout 不能短。"""
    req = urllib.request.Request(HOME_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    with open(dest, "wb") as f:
        f.write(raw)
    return len(raw)


def parse_index(path):
    """从首页 HTML 解析 data-cards 全量索引。

    坑（10-22 踩过）：属性里的引号必被转义成 &quot;，所以正则必须用
    `[^"]*`——用 `.*?` 会在第一个转义引号处提前截断。
    """
    src = open(path, encoding="utf-8").read()
    m = re.search(r'data-cards="([^"]*)"', src)
    if not m:
        raise SystemExit("首页里找不到 data-cards 属性（站点结构可能变了）")
    return json.loads(H.unescape(m.group(1)))


def load_lib_table(name):
    p = os.path.join(ROOT, "data", name + ".json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f) or []


def diff_against_library(cards):
    """与库内五表对比，返回 (新增, slug命中数, 名称命中数)。

    双判据：norm_name 主判 + slug 辅判。注意 norm_name 有公司后缀盲区
    （`Bustem, Inc.` → busteminc ≠ 库内 `Bustem`），所以 slug 命中单独统计，
    两条路径都不中的才算新增。
    """
    known_names, known_slugs = set(), set()
    for name in LIB_TABLES:
        for c in load_lib_table(name):
            nk = TC.norm_name(c.get("name"))
            if nk:
                known_names.add(nk)
            for key in ("trustmrr_slug", "slug", "htm_slug"):
                if c.get(key):
                    known_slugs.add(str(c[key]).lower())
    new_items, by_slug, by_name = [], 0, 0
    for c in cards:
        slug = (c.get("slug") or "").lower()
        nk = TC.norm_name(c.get("name"))
        if slug and slug in known_slugs:
            by_slug += 1
        elif nk and nk in known_names:
            by_name += 1
        else:
            new_items.append(c)
    return new_items, by_slug, by_name


def find_prev_seed_files(out_dir, today):
    """找 out 目录里上一轮的种子清单（排除今天），用于重叠度检测。"""
    prev = []
    for fn in os.listdir(out_dir) if os.path.isdir(out_dir) else []:
        m = re.match(r"seeds_(\d{4}-\d{2}-\d{2})\.txt$", fn)
        if m and m.group(1) != today:
            prev.append((m.group(1), os.path.join(out_dir, fn)))
    prev.sort(reverse=True)
    return prev


def read_seed_file(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                out.append(ln)
    return out


def find_prev_index(out_dir, today):
    """找上一次的索引快照，用于总数持平检测。"""
    prev = []
    for fn in os.listdir(out_dir) if os.path.isdir(out_dir) else []:
        m = re.match(r"htm_index_(\d{4}-\d{2}-\d{2})\.json$", fn)
        if m and m.group(1) != today:
            prev.append((m.group(1), os.path.join(out_dir, fn)))
    prev.sort(reverse=True)
    return prev[0] if prev else None


def load_verified_slugs(path):
    """读核验台账里已处置的 slug，避免重复核验/重复入队。"""
    if not path or not os.path.exists(path):
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {r.get("slug") for r in d.get("results", []) if r.get("slug")}
    except Exception as e:
        log("    [!] 核验台账读取失败（继续，不剔除）：%s" % e)
        return set()


# ---------------------------------------------------------------- 主流程

def run_increment(args):
    date = args.date or datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join(ROOT, args.out) if not os.path.isabs(args.out) else args.out
    os.makedirs(out_dir, exist_ok=True)
    home_path = os.path.join(out_dir, "home_%s.html" % date)

    log("=" * 62)
    log("  howtomakemi 增量管线 · %s" % date)
    log("=" * 62)

    # ① 抓首页（或复用）
    if args.skip_fetch and os.path.exists(home_path):
        log("[1/5] 复用已抓首页 %s（%d 字节）" % (home_path, os.path.getsize(home_path)))
    else:
        log("[1/5] 抓取 %s …" % HOME_URL)
        try:
            size = fetch_home(home_path)
        except Exception as e:
            log("    [!] 抓取失败：%s" % e)
            return 1
        log("    拿到 %d 字节 → %s" % (size, home_path))

    # ② 解析索引
    log("[2/5] 解析 data-cards 全量索引…")
    cards = parse_index(home_path)
    log("    索引总条数：%d" % len(cards))
    for name in LIB_TABLES:
        log("    库内 %-14s %d 条" % (name, len(load_lib_table(name))))

    # 索引快照落盘（供下次持平检测）
    idx_path = os.path.join(out_dir, "htm_index_%s.json" % date)
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)

    # 持平检测
    prev_idx = find_prev_index(out_dir, date)
    if prev_idx:
        try:
            with open(prev_idx[1], encoding="utf-8") as f:
                prev_cards = json.load(f)
            if len(prev_cards) == len(cards):
                log("    [i] 索引总数与 %s 持平（%d 条）——站点零新 slug。" % (prev_idx[0], len(cards)))
                log("        增量可能在遗留处置里（上轮拦下/误杀的），继续跑让 harvest 从档案池重新选拔。")
            else:
                log("    [i] 索引总数 %d → %d（较 %s）" % (len(prev_cards), len(cards), prev_idx[0]))
        except Exception:
            pass

    # ③ 对比 + 分型
    log("[3/5] 与库内对比…")
    new_items, by_slug, by_name = diff_against_library(cards)
    log("    slug 命中 %d / 名称命中 %d / 新增 %d" % (by_slug, by_name, len(new_items)))

    verified = load_verified_slugs(args.verified) if args.verified else set()
    seed_cards = [c for c in new_items
                  if c.get("type") in SEED_TYPES and c.get("slug")]
    skipped_verified = [c for c in seed_cards if c["slug"] in verified]
    seed_cards = [c for c in seed_cards if c["slug"] not in verified]
    registry_cards = [c for c in new_items if c.get("type") in REGISTRY_TYPES]

    log("    候选种子 %d（%s）" % (len(seed_cards) + len(skipped_verified),
                              "+".join(SEED_TYPES)))
    if skipped_verified:
        log("    其中已核验登记过、跳过：%d" % len(skipped_verified))
    log("    仅登记（%s）：%d" % ("/".join(REGISTRY_TYPES), len(registry_cards)))
    with_money = [c for c in seed_cards if MONEY_RE.search(c.get("income") or "")]
    log("    新种子中自称有收入数字：%d" % len(with_money))

    # 种子重叠度检测
    prev_seeds = find_prev_seed_files(out_dir, date)
    if prev_seeds:
        try:
            old = set(read_seed_file(prev_seeds[0][1]))
            new_slugs = {c["slug"] for c in seed_cards}
            overlap = new_slugs & old
            ratio = (len(overlap) / len(new_slugs) * 100) if new_slugs else 0
            log("    [i] 与上轮（%s）种子重叠 %d/%d（%.0f%%）" % (
                prev_seeds[0][0], len(overlap), len(new_slugs), ratio))
            if new_slugs and ratio == 100:
                log("        100% 重叠 = 站点无新 slug；继续跑的意义在于上轮被拦下的")
                log("        （过滤器误杀/低于门槛），harvest 种子模式会从档案池重新选拔。")
        except Exception:
            pass

    # 种子清单 + 登记表 + 新增清单落盘
    seeds_path = os.path.join(out_dir, "seeds_%s.txt" % date)
    with open(seeds_path, "w", encoding="utf-8") as f:
        f.write("# howtomakemi %s 增量种子：%s（%s 仅登记，已核验的已剔除）\n"
                % (date, "+".join(SEED_TYPES), "/".join(REGISTRY_TYPES)))
        for c in seed_cards:
            f.write(c["slug"] + "\n")
    new_path = os.path.join(out_dir, "htm_new_%s.json" % date)
    with open(new_path, "w", encoding="utf-8") as f:
        json.dump(new_items, f, ensure_ascii=False, indent=2)
    if registry_cards:
        reg_path = os.path.join(out_dir, "domestic_registry_%s.json" % date)
        with open(reg_path, "w", encoding="utf-8") as f:
            json.dump([{"slug": c["slug"], "name": c.get("name"),
                        "income": c.get("income")} for c in registry_cards],
                      f, ensure_ascii=False, indent=2)
        log("    国内实践登记表 → %s" % os.path.basename(reg_path))
    log("    种子清单 → %s（%d 个）" % (os.path.basename(seeds_path), len(seed_cards)))
    log("    新增清单 → %s" % os.path.basename(new_path))

    if args.dry_run:
        log("\n[*] --dry-run：到此为止，不跑采集。")
        write_report(out_dir, date, cards, new_items, seed_cards,
                     registry_cards, skipped_verified, harvested=None)
        return 0

    # ④ 跑 TrustMRR sitemap 通道
    if not seed_cards:
        log("\n[4/5] 无种子可跑（站点无新 slug 或全部已核验），跳过采集。")
        log("[5/5] 完成：本次无新增。")
        write_report(out_dir, date, cards, new_items, seed_cards,
                     registry_cards, skipped_verified, harvested=None)
        return 0

    log("\n[4/5] 跑 TrustMRR sitemap 通道（%d 个种子）…" % len(seed_cards))
    cmd = [_python_exe(), os.path.join(ROOT, "scripts", "harvest.py"),
           "--source", "trustmrr_sitemap",
           "--tm-seeds", seeds_path,
           "--tm-sweep", str(len(seed_cards)),
           "--limit", str(len(seed_cards) + 50),
           "--empty-ok"]
    log("    $ python scripts/harvest.py --source trustmrr_sitemap "
        "--tm-seeds %s --tm-sweep %d --limit %d"
        % (os.path.relpath(seeds_path, ROOT), len(seed_cards), len(seed_cards) + 50))
    log("    [i] 解释器：%s" % cmd[0])
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        log("    [!] harvest 子进程退出码 %d" % proc.returncode)
        return 2

    # ⑤ 简报
    log("\n[5/5] 采集完成，读取结果…")
    harvested = count_today(date)
    log("    今日新增入队 %d 条（inbox 总数 %d）" % (
        harvested["today"], harvested["inbox_total"]))
    write_report(out_dir, date, cards, new_items, seed_cards,
                 registry_cards, skipped_verified, harvested)
    log("\n下一步：核验 gone 线索（一手口径纪律见 skill htm-increment），"
        "\n或跑 scripts/triage.py --scope inbox 看队列分级。")
    return 0


def count_today(date):
    """读 inbox，统计今日入队条数与总数。"""
    inbox = load_lib_table("inbox")
    today = [c for c in inbox if (c.get("added_at") or "").startswith(date)]
    return {"today": len(today), "inbox_total": len(inbox)}


def write_report(out_dir, date, cards, new_items, seed_cards,
                 registry_cards, skipped_verified, harvested):
    """写一份 markdown 简报，人扫一眼就知道今天发生了什么。"""
    lines = [
        "# howtomakemi 增量简报 · %s" % date,
        "",
        "- 索引总条数：%d" % len(cards),
        "- 库内对比新增：%d" % len(new_items),
        "- 生成种子：%d（%s）" % (len(seed_cards), "+".join(SEED_TYPES)),
    ]
    if skipped_verified:
        lines.append("- 已核验登记跳过：%d" % len(skipped_verified))
    lines.append("- 仅登记（国内实践）：%d" % len(registry_cards))
    if harvested is not None:
        lines.append("- 今日入队：%d 条（inbox 总数 %d）"
                     % (harvested["today"], harvested["inbox_total"]))
    lines += ["", "## 种子清单（按索引顺序）", ""]
    for c in seed_cards:
        inc = re.sub(r"\s+", " ", (c.get("income") or ""))[:60]
        lines.append("- `%s` %s — %s" % (c.get("slug"),
                                          c.get("name") or "?", inc))
    if not seed_cards:
        lines.append("（无）")
    path = os.path.join(out_dir, "increment_report_%s.md" % date)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("    简报 → %s" % os.path.basename(path))


def main():
    ap = argparse.ArgumentParser(
        description="howtomakemi 增量管线一键入口（抓取→解析→种子→采集→报告）")
    ap.add_argument("--date", help="日期标签 YYYY-MM-DD（默认今天）")
    ap.add_argument("--out", default="tmp/htm_screen",
                    help="台账输出目录（默认 tmp/htm_screen；可指 data/htm）")
    ap.add_argument("--verified",
                    default="tmp/htm_screen/verify_results.json",
                    help="核验台账路径（剔除已处置 slug；不存在则跳过）")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="复用已抓的 home_<date>.html（调试用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只解析+对比+出报告，不跑采集")
    args = ap.parse_args()
    return run_increment(args)


if __name__ == "__main__":
    sys.exit(main())
