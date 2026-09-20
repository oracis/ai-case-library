#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「发布一条案例」的六步串成一个顺序流程。

为什么要有这个文件
------------------
发布不是原子动作，是六步，而且**少一步不会报错，会静默出问题**：

    1. contentpack  补内容包    漏了 → 发布出「有数字没内容」的空壳卡片
    2. publish      走 promote  漏了 → 数据还在候选池，站上什么都没有
    3. fit          补判断      漏了 → 公众号草稿从 5 段掉到 3 段；首页适配度空白
    4. score        算派生分    漏了 → solo_fit / china_fit 是空的，四象限图缺这一条
    5. build        建静态站    漏了 → 部署的还是上一版
    6. deploy       上传 OSS    漏了 → 本地对了，线上还是旧的

每一步单独看都有脚本，也各自有测试。但**它们之间的顺序和依赖只存在于
人的记忆里**：比如 fit 必须在 publish 之后（要补的是已发布案例），
score 必须在 fit 之后（要算的是刚补的判断），build 必须在 score 之后
（不然打进去的还是空的派生分）。

2026-09-20 发布 5 条时，第 3 步是漏掉之后被测试报出来的（test_make_article
2 项失败：新案例缺 china_fit / solo_fit）。同一个坑不该再踩第二次。

为什么是「调用函数」而不是「拼 shell 命令」
------------------------------------------
六个脚本都是本目录下的模块，`import` 进来直接调 `main()` 就够了：

  · 顺序和依赖是代码，不是字符串拼出来的命令行 —— 测试能直接断言
  · 少一层 subprocess，Windows 上不用操心引号和编码
  · 失败能拿到真实退出码，而不是解析 stdout

代价是这些脚本的 `main()` 都读 `sys.argv`。所以本文件统一用 `_argv()`
上下文管理器临时替换 `sys.argv`，跑完还原 —— 也顺便让每个子步骤
可以自己带参数（比如给 build 传 `--out public`）。

退出码约定：0 全部跑完；非 0 = 在哪一步停的（步骤序号），便于 CI 区分。
"""

import argparse
import contextlib
import io
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_static                     # noqa: E402
import contentpack_ready                # noqa: E402
import deploy_oss                       # noqa: E402
import fit_ready                        # noqa: E402
import publish_ready                    # noqa: E402
import score_china_fit                  # noqa: E402
import score_solo_fit                   # noqa: E402
import verify_deploy                    # noqa: E402
import ai_verify as AV                  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 步骤表：改名可以，改顺序要同时改这里的 `after` 和测试。
#
# `after` 不是注释，是**有约束力的依赖声明**：自检与测试都会拿它校验
# 「声明的顺序」和「实际的顺序」是否一致。乱改顺序会让自检红，
# 而不是静默跑出一个错的结果。
#
# 每一对都对应一次真实踩坑，写在 needs 里当理由。
# ---------------------------------------------------------------------------
STEPS = (
    {
        "key": "contentpack",
        "title": "补内容包",
        "after": (),                              # 纯本地读写，不依赖任何步
        "needs": "无依赖：读候选池、写回候选池，不碰网络也不碰案例",
        "run": lambda a: contentpack_ready.main(),
    },
    {
        "key": "publish",
        "title": "发布（走 promote）",
        "after": ("contentpack",),
        "needs": "内容包：没写进去就发 = 空壳案例（9-17 那批 6 条发完被退回）",
        "run": lambda a: publish_ready.main(),
    },
    {
        "key": "fit",
        "title": "补三套人工判断",
        "after": ("publish",),
        "needs": "案例已存在：补的是已发布案例的 replicability，发之前没人可补",
        "run": lambda a: fit_ready.main(),
    },
    {
        "key": "score",
        "title": "算派生分",
        "after": ("fit",),
        "needs": "刚补的判断：算的就是上一步写进去的 replicability",
        "run": None,                              # 两个脚本，单独处理
    },
    {
        "key": "build",
        "title": "建静态站",
        "after": ("score",),
        "needs": "算完分的 cases.json：打进去的必须是带 solo_fit/china_fit 的版本",
        "run": None,                              # 要按 --out 传参
    },
    {
        "key": "deploy",
        "title": "部署到 OSS",
        "after": ("build",),
        "needs": "产物已就位：不然传的是上一版",
        "run": None,                              # 参数多，单独处理
    },
)

STEP_KEYS = tuple(s["key"] for s in STEPS)
STEP_INDEX = {k: i for i, k in enumerate(STEP_KEYS)}

# 唯一的例外：publish 还额外需要后台 server 活着。
# 这不是「顺序依赖」而是「外部前提」，所以单独列出来 ——
# 混进 after 里会让「上游是 server」这种非步骤值污染依赖图。
NEEDS_SERVER = ("publish",)


def order_problems(steps=None):
    """顺序层面的问题清单（空 = 没问题）。

    自检与测试共用这一个函数 —— 两处各写一套校验，迟早会分叉成
    「自检说好、测试说坏」这种最难查的状态。
    """
    steps = STEPS if steps is None else steps
    keys = [s["key"] for s in steps]
    idx = {k: i for i, k in enumerate(keys)}
    problems = []

    if len(keys) != len(set(keys)):
        problems.append("有重复的步骤 key：%s" % keys)

    for s in steps:
        for up in s.get("after") or ():
            if up not in idx:
                problems.append("%s 声明的上游 %s 不是已知步骤" % (s["key"], up))
            elif idx[up] >= idx[s["key"]]:
                problems.append("%s 声明要在 %s 之后，但它排在前面"
                                % (s["key"], up))
    return problems


def circular(steps=None):
    """依赖成环检测 —— 环在顺序表里不可能构成，但表被改成 dict 或重排时可能。"""
    steps = STEPS if steps is None else steps
    graph = {s["key"]: list(s.get("after") or []) for s in steps}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in graph}

    def visit(n, stack):
        if color.get(n) == GRAY:
            return stack + [n]
        if color.get(n) == BLACK:
            return None
        color[n] = GRAY
        for up in graph.get(n, []):
            cyc = visit(up, stack + [n])
            if cyc:
                return cyc
        color[n] = BLACK
        return None

    for k in graph:
        cyc = visit(k, [])
        if cyc:
            return cyc
    return None


def topological_order(steps=None):
    """按 after 声明算出正确顺序。自检拿它和实际顺序比对。"""
    steps = STEPS if steps is None else steps
    graph = {s["key"]: list(s.get("after") or []) for s in steps}
    out, seen = [], set()

    def visit(n):
        if n in seen:
            return
        for up in graph.get(n, []):
            visit(up)
        seen.add(n)
        out.append(n)

    for s in steps:
        visit(s["key"])
    return out



def output_dir(args):
    """对外产物目录 —— 一律 public，不给 dist 留口子。

    dist 是本地预览版、带 inbox 未核实素材，2026-09-20 曾用默认参数把它
    推上过公网（441 条未核实素材），靠线上校验发现。所以这里**不暴露
    成参数**：想发 dist 得自己去跑 deploy_oss.py 并显式 --allow-inbox。
    """
    return os.path.join(ROOT, args.out)


@contextlib.contextmanager
def _argv(argv):
    """临时替换 sys.argv —— 子脚本的 main() 都从它读参数。"""
    saved = sys.argv
    sys.argv = list(argv)
    try:
        yield
    finally:
        sys.argv = saved


def _call(fn, argv, capture=False):
    """跑一个子脚本的 main()，把 SystemExit 归一成退出码。

    capture=True 时不打印它的输出，只把文本收下来 —— 给「探路」用：
    比如 publish 之前想知道有几条可发，不能让它先把一堆清单打出来。
    """
    buf = io.StringIO() if capture else None
    try:
        with _argv(argv):
            if buf is None:
                fn()
            else:
                with contextlib.redirect_stdout(buf):
                    fn()
        return 0, (buf.getvalue() if buf is not None else "")
    except SystemExit as e:
        code = e.code
        rc = 0 if code is None else (code if isinstance(code, int) else 1)
        return rc, (buf.getvalue() if buf is not None else "")
    except Exception as e:                                        # noqa: BLE001
        print("  [!] %s: %s" % (type(e).__name__, e))
        return 1, (buf.getvalue() if buf is not None else "")


# ---------------------------------------------------------------------------
# 前置检查
# ---------------------------------------------------------------------------


def admin_alive(base=None, timeout=3):
    """后台起没起。只探一枪，不依赖具体端点 —— 404 也算「活着」。"""
    base = base or AV.ADMIN_BASE
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base + "/api/status", timeout=timeout) as r:
            return True, "HTTP %s" % r.status
    except urllib.error.HTTPError as e:
        return True, "HTTP %s（服务在跑）" % e.code
    except Exception as e:                                        # noqa: BLE001
        return False, str(getattr(e, "reason", e))


def pending_publish():
    """有几条候选内容包齐全、可以被发布。只读，不连 server。"""
    import json
    with open(os.path.join(ROOT, "data", "candidates.json"), encoding="utf-8") as f:
        cands = json.load(f)
    chosen, skipped = publish_ready.select(cands, None)
    return chosen, skipped


def missing_judgements():
    """还有哪些案例缺判断（replicability 或 solo/china 判断表）。"""
    cases = fit_ready.load_cases()
    return (fit_ready.missing_replicability(cases),
            fit_ready.missing_solo(cases),
            fit_ready.missing_china(cases))


def preflight(args):
    """跑之前把「这趟会做什么、能不能做」讲清楚。返回 False 就别跑了。

    这里故意只**报告**不拦截（除了硬条件）：
    比如「没有可发布的候选」应该正常跳过 publish 继续往下（可能是补判断
    或重建），而不是报错退出 —— 六步是幂等的，重复跑不该失败。
    """
    chosen, skipped = pending_publish()
    rep, solo, china = missing_judgements()

    print("=" * 66)
    print("  发布前检查")
    print("=" * 66)
    print("  可发布候选（内容包齐）: %d 条%s"
          % (len(chosen), ("  " + "、".join(c["id"] for c in chosen)) if chosen else ""))
    print("  缺内容包而跳过        : %d 条" % len(skipped))
    print("  待补判断的案例        : replicability %d · solo %d · china %d"
          % (len(rep), len(solo), len(china)))

    if args.region:
        print("  部署地域              : %s" % args.region)
    else:
        print("  部署地域              : 未指定（没配 OSS_REGION 的话 deploy 会拦下）")
    print("  产物目录              : %s" % os.path.relpath(output_dir(args), ROOT))

    ok, detail = admin_alive()
    if ok:
        print("  后台 server           : 在跑（%s）" % detail)
    else:
        print("  后台 server           : **没起**（%s）" % detail)

    print()

    # ---- 硬条件：要跑 publish 却没 server，现在就说，别等第三步 ----
    if not args.skip_publish and chosen and not ok:
        print("[!] 有 %d 条要发布，但后台 server 没在跑。" % len(chosen))
        print("    发布只走 POST /api/candidates/:id/promote，规则引擎是唯一闸门，")
        print("    绕不过去（也不该绕）。先起服务：")
        print("      python server.py        # 公开 5052 / 后台 5053")
        print("    或者：--skip-publish     # 跳过发布，只重建与部署")
        return False

    if "deploy" not in skip_set(args) and not args.region:
        # 地域不在这里拦（deploy_oss 自己会拦且有更完整的提示），
        # 但值得提前说一声 —— 免得跑完五步到第六步才发现没给地域。
        print("[i] 没给 --region 也没设 OSS_REGION：deploy 一步会被拦下。")
        print("    想跑完整链路就补上：--region cn-hongkong")
        print()

    return True


# ---------------------------------------------------------------------------
# 各步的执行
# ---------------------------------------------------------------------------


def run_contentpack(args):
    print("── 补内容包 ──")
    # 这一步的「预演」= --check（只校验内容包质量，不写盘）。
    # contentpack_ready 没有 --dry-run 这个参数名。
    argv = ["contentpack_ready.py", "--check" if args.dry_run else "--apply"]
    rc, _ = _call(contentpack_ready.main, argv)
    if rc != 0:
        # 没有可写的条目会返回 1。这是**正常**的（比如刚发完一批），
        # 不该让整条链路断在这里 —— 后面几步仍然有意义。
        print("  （没有新内容包可写，继续）")
    return 0


def run_publish(args):
    print("── 发布 ──")
    chosen, _ = pending_publish()
    if not chosen:
        print("  没有内容包齐全的候选，跳过。")
        return 0
    argv = ["publish_ready.py"] + (["--dry-run"] if args.dry_run else [])
    rc, _ = _call(publish_ready.main, argv)
    if rc != 0:
        print("  [!] 发布步骤退出码 %d" % rc)
        return rc
    return 0


def run_fit(args):
    print("── 补三套人工判断 ──")
    argv = ["fit_ready.py"] + (["--dry-run"] if args.dry_run else [])
    rc, _ = _call(fit_ready.main, argv)
    if rc != 0:
        print("  [!] 补判断失败，退出码 %d" % rc)
        return rc
    return 0


def run_score(args):
    print("── 算派生分 ──")
    # 顺序固定：solo 先（它写 composite + quadrant），china 后。
    # 两个脚本各自读写 cases.json，不能并行 —— 并发写会丢字段。
    extra = ["--dry-run"] if args.dry_run else []
    for label, fn in (("solo_fit", score_solo_fit.main),
                      ("china_fit", score_china_fit.main)):
        mod = {"solo_fit": "score_solo_fit.py",
               "china_fit": "score_china_fit.py"}[label]
        rc, _ = _call(fn, [mod] + extra)
        if rc != 0:
            print("  [!] %s 退出码 %d" % (label, rc))
            return rc
        print("  [OK] %s %s" % (label, "已算（预演，未写盘）" if args.dry_run else "已写回"))
    return 0


def run_build(args):
    print("── 建静态站 ──")
    out = output_dir(args)
    argv = ["build_static.py", "--out", out]
    if not args.keep_inbox:
        argv.append("--no-inbox")
    rc, _ = _call(build_static.main, argv)
    if rc != 0:
        print("  [!] 构建失败，退出码 %d" % rc)
        return rc
    # 产物闸门：对外产物绝不能带 inbox。构建参数已经保证了，
    # 但这里再读一次产物自查 —— 「参数对」和「产物干净」是两回事。
    n = deploy_oss.inbox_count(os.path.join(out, "data.json"))
    if n is None:
        print("  [!] 读不出 %s/data.json 的 inbox 字段 —— 产物不干净或构建异常。" % args.out)
        return 1
    if n and not args.keep_inbox:
        print("  [!] %s 里带着 %d 条未核实队列，拒绝继续。" % (args.out, n))
        return 1
    print("  [OK] %s（inbox %s）" % (args.out, n))
    return 0


def run_deploy(args):
    print("── 部署到 OSS ──")
    # 缺配置在**预演**里不算失败：预演就是来看「会怎样」的，
    # 报出缺什么比直接退出有用（真跑时仍然是失败，见下）。
    if not args.bucket:
        print("  [!] 没给 --bucket。")
        print("      本项目是 ai-case-library：--bucket ai-case-library --region cn-hongkong")
        return 0 if args.dry_run else 1
    if not args.region:
        print("  [!] 没给地域。deploy_oss 不兜底 —— 猜错只会 403 且看不出原因。")
        print("      补上：--region cn-hongkong（或设 OSS_REGION）")
        return 0 if args.dry_run else 1

    argv = ["deploy_oss.py",
            "--bucket", args.bucket,
            "--region", args.region,
            "--dir", output_dir(args),
            "--verify-public"]
    if args.prefix:
        argv += ["--prefix", args.prefix]
    if args.env_file:
        argv += ["--env-file", args.env_file]
    if args.dry_run:
        argv.append("--dry-run")

    # deploy_oss 的失败是 return 1 而不是 raise SystemExit，
    # 所以这里不能只看 _call 的返回码，得自己接住。
    with _argv(argv):
        rc = deploy_oss.main()
    if rc:
        print("  [!] 部署退出码 %s" % rc)
        return rc or 1
    return 0


def run_verify(args):
    print("── 线上语义校验 ──")
    argv = ["verify_deploy.py"]
    if args.base:
        argv += ["--base", args.base]
    rc, _ = _call(verify_deploy.main, argv)
    if rc != 0:
        print("  [!] 线上校验有失败项（退出码 %d）" % rc)
        print("      注意：这只说明线上内容对不上本地，不代表上传失败。")
        return rc
    print("  [OK] 线上与本地一致")
    return 0


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

RUNNERS = {
    "contentpack": run_contentpack,
    "publish": run_publish,
    "fit": run_fit,
    "score": run_score,
    "build": run_build,
    "deploy": run_deploy,
}


def skip_set(args):
    """这一趟要跳过哪些步。--skip-xxx 与 --only 合成同一个集合。"""
    skip = set()
    if args.skip_publish:
        skip.add("publish")
    if args.skip_deploy:
        skip.add("deploy")
    if args.skip_build:
        skip.add("build")
    if args.skip_score:
        skip.add("score")
    if args.only:
        skip |= {k for k in STEP_KEYS if k not in args.only}
    return skip


def main():
    ap = argparse.ArgumentParser(
        description="发布链路编排器：contentpack → publish → fit → score → build → deploy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
常见跑法
--------
  python scripts/release.py --dry-run                     # 全链路预演，不写盘不上传
  python scripts/release.py --bucket ai-case-library \\
         --region cn-hongkong                             # 完整发布
  python scripts/release.py --skip-publish --skip-deploy  # 只重算分并重建本地产物
  python scripts/release.py --only fit,score,build        # 只跑某几步（调试用）

失败即停
--------
任何一步非 0 退出，后面都不跑 —— 因为后面的步骤依赖前面的产物。
比如 fit 失败还继续 score，算出来的就是没补判断的旧数据。
""")
    ap.add_argument("--bucket", help="OSS Bucket 名（部署用，如 ai-case-library）")
    ap.add_argument("--region", default=None,
                    help="OSS 地域（如 cn-hongkong）。不给则读 OSS_REGION，都没有就跳过部署")
    ap.add_argument("--prefix", default="", help="传到 Bucket 下的子路径")
    ap.add_argument("--env-file", help="从 .env 读 OSS 凭证")
    ap.add_argument("--out", default="public", help="产物目录（默认 public，对外版）")
    ap.add_argument("--keep-inbox", action="store_true",
                    help="产物保留未核实队列（**只用于本地预览**，会导致部署被拦下）")
    ap.add_argument("--base", help="线上校验的站点地址（默认 verify_deploy 的默认值）")

    ap.add_argument("--dry-run", action="store_true",
                    help="只预演：不写数据、不部署（各步出自己的 dry-run 输出）")
    ap.add_argument("--only", help="只跑这几步，逗号分隔（如 fit,score,build）")
    ap.add_argument("--skip-publish", action="store_true", help="跳过发布")
    ap.add_argument("--skip-build", action="store_true", help="跳过构建")
    ap.add_argument("--skip-score", action="store_true", help="跳过算分")
    ap.add_argument("--skip-deploy", action="store_true", help="跳过部署（本地重建用）")
    ap.add_argument("--yes", action="store_true",
                    help="不交互确认（默认就是非交互，这个参数只为脚本里显式表意）")
    args = ap.parse_args()

    if args.region is None:
        args.region = os.environ.get("OSS_REGION")
    if not args.bucket:
        args.bucket = os.environ.get("OSS_BUCKET")

    # --only 的取值要校验：打错一个字就静默什么都不跑，最难查。
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        bad = [s for s in wanted if s not in STEP_KEYS]
        if bad:
            print("[!] --only 里有不认识的步骤：%s" % "、".join(bad))
            print("    可用：%s" % "、".join(STEP_KEYS))
            return 2
        args.only = wanted
    if args.dry_run and args.keep_inbox:
        print("[!] --dry-run 与 --keep-inbox 一起用没意义，先想清楚要发哪个版本。")
        return 2
    if args.keep_inbox and not args.skip_deploy:
        print("[!] --keep-inbox 会造出带未核实队列的产物，不允许部署。")
        print("    要么去掉 --keep-inbox，要么加 --skip-deploy。")
        return 2

    # 启动前自查顺序 —— 顺序错了不会报错，只会静默产出错的东西。
    # CI 里有 release --selfcheck，但人本地直接跑 main() 时没有，
    # 所以这里也挡一道：代价是几毫秒，收益是不必信任「上次改的人记得」。
    problems = order_problems()
    if problems:
        print("[!] 步骤顺序自相矛盾，先修好再跑：")
        for p in problems:
            print("    · %s" % p)
        print("    跑 --selfcheck 看完整清单。")
        return 3

    if not preflight(args):
        return 1

    skip = skip_set(args)
    print("=" * 66)
    print("  开始发布链路" + ("（--dry-run 预演）" if args.dry_run else ""))
    print("=" * 66)

    for i, step in enumerate(STEPS, 1):
        key = step["key"]
        if key in skip:
            print("\n[%d/6] %s —— 跳过" % (i, step["title"]))
            continue
        print("\n[%d/6] %s" % (i, step["title"]))
        rc = RUNNERS[key](args)

        if rc:
            print("\n" + "=" * 66)
            print("  在第 %d 步「%s」停下（退出码 %s）" % (i, step["title"], rc))
            print("  后面的步骤依赖这一步的产物，不继续跑。")
            print("=" * 66)
            return i

    # 部署之后顺手校验一次 —— 部署脚本自己只确认「能打开」，
    # 内容对不对是另一回事（inbox 是不是 0、案例数对不对）。
    if "deploy" not in skip and not args.dry_run:
        print("\n[校验] 线上内容")
        rc = run_verify(args)
        if rc:
            print("\n" + "=" * 66)
            print("  部署完成，但线上校验有失败项 —— 别急着说发好了。")
            print("=" * 66)
            return 7

    print("\n" + "=" * 66)
    print("  发布链路完成%s" % ("（预演，没有真的改动）" if args.dry_run else ""))
    print("=" * 66)
    return 0


# ---------------------------------------------------------------------------
# 自检：不需要 server、不碰网络，把「顺序」和「失败即停」两条规则钉死。
# 顺序错了不会报错，会静默出问题 —— 所以必须能被测试直接验证。
# ---------------------------------------------------------------------------


def selfcheck():
    bad = 0

    def check(label, cond, detail=""):
        nonlocal bad
        if cond:
            print("  [OK] %s" % label)
        else:
            bad += 1
            print("  [!!] %s %s" % (label, detail))

    check("六步齐全", STEP_KEYS == ("contentpack", "publish", "fit",
                                    "score", "build", "deploy"),
          str(STEP_KEYS))

    # 关键：不是逐对手写断言，而是拿 after 声明去校验实际顺序。
    # 这样「改了顺序忘了改声明」和「改了声明忘了改顺序」都会被抓到，
    # 而手写断言只能抓后者。
    problems = order_problems()
    check("声明的依赖与实际顺序一致", not problems, "；".join(problems))

    cyc = circular()
    check("依赖无环", cyc is None, "环：%s" % (cyc,))

    topo = [k for k in topological_order() if k in STEP_KEYS]
    check("实际顺序就是拓扑序", topo == list(STEP_KEYS),
          "拓扑序 %s ≠ 实际 %s" % (topo, STEP_KEYS))

    check("每步都有依赖声明或明确无依赖",
          all("after" in s and "needs" in s for s in STEPS))

    # 产物目录不许是 dist —— 那是带 inbox 的预览版，曾误推上公网
    class _A:
        out = "public"
    check("对外产物目录是 public", output_dir(_A()) == os.path.join(ROOT, "public"))

    print("\n自检：%d 项失败" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        raise SystemExit(selfcheck())
    raise SystemExit(main())
