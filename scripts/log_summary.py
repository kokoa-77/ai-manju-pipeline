#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
log_summary.py —— 汇总「生成日志.csv」：抽卡率、废片原因、工时、花费。

做满 3 条作品之后跑一次，「每段抽 3–5 次」「单集多少钱」就从拍脑袋变成你自己的数据。

用法：
    python log_summary.py --log 生成日志.csv
    python log_summary.py --log 生成日志.csv --out 抽卡复盘.md

日志列名见 assets/生成日志模板.csv：
    日期,编号,镜头号,镜头类型,工具,模型版本,结果,废片原因,耗时(分钟),花费
「镜头号」也可以写成「段号」。「结果」填 过 / 修 / 废。

抽卡率 = 某个镜头一共生成了几次，才拿到「过」。没拿到「过」的镜头单独列出来。
"""

import argparse
import csv
import re
import sys
from collections import Counter, OrderedDict

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def read_csv(path):
    last = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            return [{(k or "").strip(): (v or "").strip() for k, v in r.items() if k} for r in rows]
        except UnicodeDecodeError as e:
            last = e
    raise SystemExit(f"[错误] 读不了 {path}：{last}")


def num(s):
    m = re.search(r"-?\d+(?:\.\d+)?", s or "")
    return float(m.group(0)) if m else 0.0


def pick(row, *names):
    for n in names:
        if row.get(n):
            return row[n]
    return ""


def group_stats(rows, key):
    """按 key 分组：生成次数、过/修/废、过率、平均每个镜头抽几次、耗时、花费。"""
    groups = OrderedDict()
    for r in rows:
        g = groups.setdefault(key(r) or "（未填）", {"n": 0, "res": Counter(), "min": 0.0, "cost": 0.0, "shots": {}})
        g["n"] += 1
        g["res"][r["_res"]] += 1
        g["min"] += r["_min"]
        g["cost"] += r["_cost"]
        shot = g["shots"].setdefault(r["_shot"], {"tries": 0, "passed": False})
        shot["tries"] += 1
        if r["_res"] == "过":
            shot["passed"] = True
    return groups


def tries_per_pass(shots):
    done = [s["tries"] for s in shots.values() if s["passed"]]
    return f"{sum(done) / len(done):.1f}" if done else "—"


def table(title, groups):
    out = ["", f"## {title}", "",
           "| 分组 | 生成次数 | 过 / 修 / 废 | 过率 | 每个镜头平均抽几次 | 耗时 | 每个镜头平均耗时 | 花费 |",
           "|---|---|---|---|---|---|---|---|"]
    for k, g in groups.items():
        rate = g["res"]["过"] / g["n"] if g["n"] else 0
        per_shot = g["min"] / len(g["shots"]) if g["shots"] else 0
        out.append(f"| {k} | {g['n']} | {g['res']['过']} / {g['res']['修']} / {g['res']['废']} | "
                   f"{rate:.0%} | {tries_per_pass(g['shots'])} | {g['min']/60:.1f}h | {per_shot:.0f} 分钟 | {g['cost']:g} |")
    return out


def main():
    ap = argparse.ArgumentParser(description="生成日志汇总")
    ap.add_argument("--log", required=True, help="生成日志 CSV")
    ap.add_argument("--out", default="", help="写成 md 文件（不填则只打印）")
    args = ap.parse_args()

    raw = read_csv(args.log)
    rows = []
    for r in raw:
        if not any(r.values()):
            continue
        res = r.get("结果", "")
        res = "过" if "过" in res else ("修" if "修" in res else ("废" if "废" in res else res or "（未填）"))
        r["_res"] = res
        r["_shot"] = f"{r.get('编号','')}/{pick(r, '镜头号', '段号')}"
        r["_min"] = num(pick(r, "耗时(分钟)", "耗时（分钟）", "耗时"))
        r["_cost"] = num(r.get("花费", ""))
        rows.append(r)
    if not rows:
        raise SystemExit("[错误] 生成日志是空的。")

    allg = group_stats(rows, lambda r: "全部")["全部"]
    unfinished = [s for s, v in allg["shots"].items() if not v["passed"]]
    reasons = Counter(r.get("废片原因", "") for r in rows if r["_res"] in ("废", "修") and r.get("废片原因"))

    out = [f"# 生成日志汇总 · {args.log}", ""]
    out.append(f"- 生成 {allg['n']} 次，涉及 {len(allg['shots'])} 个镜头；"
               f"过 {allg['res']['过']} / 修 {allg['res']['修']} / 废 {allg['res']['废']}")
    out.append(f"- 拿到「过」的镜头平均抽 **{tries_per_pass(allg['shots'])}** 次")
    out.append(f"- 总耗时 {allg['min']/60:.1f} 小时，总花费 {allg['cost']:g}")
    if unfinished:
        out.append(f"- 还没「过」的镜头 {len(unfinished)} 个：{'、'.join(unfinished)}")

    out += table("按作品 / 集数", group_stats(rows, lambda r: r.get("编号", "")))
    out += table("按工具", group_stats(rows, lambda r: r.get("工具", "")))
    if any(r.get("镜头类型") for r in rows):
        out += table("按镜头类型（报价用「每个镜头平均耗时」，见 references/15 第四节）",
                     group_stats(rows, lambda r: r.get("镜头类型", "")))
    if any(r.get("模型版本") for r in rows):
        out += table("按工具 + 模型版本（模型升级后对比用）",
                     group_stats(rows, lambda r: f"{r.get('工具','')} {r.get('模型版本','')}".strip()))

    out += ["", "## 废片 / 返修原因 Top 5", ""]
    if reasons:
        for reason, n in reasons.most_common(5):
            out.append(f"- {reason}：{n} 次")
    else:
        out.append("- （没填废片原因。下次废片时写一句为什么，复盘才有用）")

    text = "\n".join(out) + "\n"
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[OK] 已写入：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
