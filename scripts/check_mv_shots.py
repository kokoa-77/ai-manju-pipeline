#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_mv_shots.py —— 拼贴风 MV 分镜表校验 + 统计。

用法：
    python check_mv_shots.py --shots 01_脚本/01_MV分镜表.csv
    python check_mv_shots.py --shots 01_脚本/01_MV分镜表.csv --out 分镜体检.md

分镜表列名见 assets/MV分镜表模板.csv：
    段号,歌曲段落,小节,拍点时间,时长,镜头类型,画面内容,素材来源,AI工具,歌词,备注

检查：
    错误：缺必需列 / 段号重复 / 拍点时间写法错 / 镜头类型不是 1–12
    警告：相邻镜头之间有空档或重叠 / 「时长」和拍点区间对不上 /
          AI 作画镜头（类型 7）时长占比超过 40%
    提示：单镜头超过 8 秒 / 短于 0.5 秒
统计：总时长、镜头数、平均镜头时长、各镜头类型占比、各歌曲段落节奏。

退出码：0 = 没有错误，1 = 有错误。
"""

import argparse
import csv
import re
import sys
from collections import OrderedDict

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SHOT_TYPES = {
    1: "实拍空镜 + Q版贴纸角色",
    2: "周边实拍",
    3: "手账拼贴转场",
    4: "手写大字标题 / 角色名卡",
    5: "剪影色块",
    6: "2.5D 场景 + 2D 角色",
    7: "高潮作画镜头（AI 视频）",
    8: "Q版扁平动画段",
    9: "宫格蒙太奇",
    10: "画中画 + 侧边文字",
    11: "宽银幕 + 细衬线歌词",
    12: "手写信 + 职员表结尾",
}
AI_HEAVY = {7}
REQUIRED = ("段号", "拍点时间", "镜头类型")
TOL = 0.05


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


def parse_t(tok):
    """'3.8' / '3.8s' / '0:03.8' -> 秒"""
    tok = tok.strip().rstrip("sS秒")
    if ":" in tok:
        m, s = tok.split(":", 1)
        return int(m) * 60 + float(s)
    return float(tok)


def parse_range(text):
    t = (text or "").strip()
    for ch in ("～", "~", "—", "–", "－", "至"):
        t = t.replace(ch, "-")
    t = re.sub(r"\s+", "", t)
    if t.count("-") != 1:
        return None
    a, b = t.split("-")
    try:
        s, e = parse_t(a), parse_t(b)
    except ValueError:
        return None
    return (s, e) if e > s else None


def main():
    ap = argparse.ArgumentParser(description="拼贴风 MV 分镜表校验 + 统计")
    ap.add_argument("--shots", required=True, help="MV 分镜表 CSV")
    ap.add_argument("--out", default="", help="把报告写成 md 文件（不填则只打印）")
    args = ap.parse_args()

    rows = read_csv(args.shots)
    errors, warns, tips = [], [], []
    if not rows:
        raise SystemExit("[错误] 分镜表是空的。")
    missing = [c for c in REQUIRED if c not in rows[0]]
    if missing:
        raise SystemExit(f"[错误] 缺少必需列：{missing}（列名见 assets/MV分镜表模板.csv）")

    shots, seen = [], set()
    for i, r in enumerate(rows, start=2):
        sid = r.get("段号", "")
        if not sid:
            continue
        if sid in seen:
            errors.append(f"第 {i} 行：段号 {sid} 重复")
        seen.add(sid)

        rng = parse_range(r.get("拍点时间", ""))
        if rng is None:
            errors.append(f"{sid}：拍点时间 `{r.get('拍点时间','')}` 写法错，应为 `7.6-9.5` 或 `0:07.6-0:09.5`")
            continue

        raw_type = r.get("镜头类型", "")
        m = re.fullmatch(r"\d+", raw_type)
        stype = int(raw_type) if m else None
        if stype not in SHOT_TYPES:
            errors.append(f"{sid}：镜头类型 `{raw_type}` 不是 1–12（对照 references/13 第三节）")

        dur = rng[1] - rng[0]
        given = r.get("时长", "")
        if given:
            try:
                g = float(re.sub(r"[^\d.]", "", given))
                if abs(g - dur) > TOL:
                    warns.append(f"{sid}：「时长」写 {g:g}s，但拍点区间是 {dur:.2f}s")
            except ValueError:
                warns.append(f"{sid}：「时长」`{given}` 不是数字")
        if dur > 8:
            tips.append(f"{sid}：单镜头 {dur:.1f}s，MV 里偏长，确认画面里有足够的变化")
        elif dur < 0.5:
            tips.append(f"{sid}：单镜头 {dur:.2f}s，太短，观众可能看不清")

        shots.append({"id": sid, "start": rng[0], "end": rng[1], "dur": dur,
                      "type": stype, "part": r.get("歌曲段落", "") or "（未标）"})

    shots.sort(key=lambda s: s["start"])
    for a, b in zip(shots, shots[1:]):
        if b["start"] > a["end"] + TOL:
            warns.append(f"{a['id']} → {b['id']}：中间空了 {b['start']-a['end']:.2f}s（{a['end']:g}s 到 {b['start']:g}s 没有画面）")
        elif b["start"] < a["end"] - TOL:
            warns.append(f"{a['id']} → {b['id']}：重叠 {a['end']-b['start']:.2f}s")

    total = sum(s["dur"] for s in shots)
    by_type = OrderedDict((k, [0, 0.0]) for k in SHOT_TYPES)
    for s in shots:
        if s["type"] in by_type:
            by_type[s["type"]][0] += 1
            by_type[s["type"]][1] += s["dur"]
    ai_dur = sum(by_type[k][1] for k in AI_HEAVY)
    if total and ai_dur / total > 0.4:
        warns.append(f"AI 作画镜头（类型 7）占全片 {ai_dur/total:.0%}，成本和崩坏风险都高；"
                     "能用 AE 分层做的镜头不要进视频模型")

    by_part = OrderedDict()
    for s in shots:
        p = by_part.setdefault(s["part"], [0, 0.0])
        p[0] += 1
        p[1] += s["dur"]

    out = [f"# MV 分镜体检 · {args.shots}", ""]
    out.append(f"- 镜头数：{len(shots)}　总时长：{total:.1f}s　平均镜头：{(total/len(shots) if shots else 0):.2f}s")
    used = sum(1 for v in by_type.values() if v[0])
    out.append(f"- 用到的镜头类型：{used} / 12")
    out.append(f"- AI 作画镜头占比：{(ai_dur/total if total else 0):.0%}")
    out += ["", "## 镜头类型", "", "| # | 类型 | 镜头数 | 时长 | 占比 |", "|---|---|---|---|---|"]
    for k, (n, d) in by_type.items():
        if n:
            out.append(f"| {k} | {SHOT_TYPES[k]} | {n} | {d:.1f}s | {d/total:.0%} |")
    out += ["", "## 歌曲段落节奏", "", "| 段落 | 镜头数 | 时长 | 平均镜头 |", "|---|---|---|---|"]
    for p, (n, d) in by_part.items():
        out.append(f"| {p} | {n} | {d:.1f}s | {d/n:.2f}s |")
    out += ["", f"## 错误 {len(errors)}", ""] + [f"- ✗ {e}" for e in errors] + (["- （无）"] if not errors else [])
    out += ["", f"## 警告 {len(warns)}", ""] + [f"- ⚠️ {w}" for w in warns] + (["- （无）"] if not warns else [])
    out += ["", f"## 提示 {len(tips)}", ""] + [f"- ℹ️ {t}" for t in tips] + (["- （无）"] if not tips else [])
    verdict = "✓ 通过" if not errors else f"✗ 有 {len(errors)} 个错误需要修"
    out += ["", f"**结论：{verdict}（{len(warns)} 个警告 / {len(tips)} 条提示）**"]
    text = "\n".join(out) + "\n"

    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[OK] 报告已写入：{args.out}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
