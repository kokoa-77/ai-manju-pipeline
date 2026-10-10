#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_srt.py —— 分镜表直接导出 SRT 字幕，导进 PR / AE / 剪映，不用再语音识别改错字。

用法：
    # 漫剧分镜表：台词按段顺序累加成整集时间轴
    python make_srt.py --shots 01_剧本/第01集_分镜表.csv --out 第01集.srt
    python make_srt.py --shots 01_剧本/第01集_分镜表.csv --out 第01集.srt --speaker

    # MV 分镜表：按「拍点时间」导出「歌词」列
    python make_srt.py --shots 01_脚本/01_MV分镜表.csv --out 01_歌词.srt

自动识别：表里有「歌词」+「拍点时间」列 → MV 模式；否则按漫剧分镜表处理。

    --speaker    漫剧模式下在每句前加「说话人：」
    --offset     整体平移秒数（成片前面有片头时用），默认 0

歌词里用「|」分行，可以做双语：`I get to see a smile again|又能看见你的笑容`
"""

import argparse
import csv
import re
import sys

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


def parse_t(tok):
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


def num(s):
    m = re.search(r"\d+(?:\.\d+)?", s or "")
    return float(m.group(0)) if m else None


def srt_time(sec):
    ms = int(round(max(sec, 0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cues_mv(rows, warns):
    cues = []
    for r in rows:
        text = r.get("歌词", "")
        if not text:
            continue
        rng = parse_range(r.get("拍点时间", ""))
        if rng is None:
            warns.append(f"{r.get('段号','?')}：拍点时间 `{r.get('拍点时间','')}` 解析不了，跳过")
            continue
        cues.append((rng[0], rng[1], text.replace("|", "\n")))
    return cues


def cues_manju(rows, speaker, warns):
    order, seg_rows = [], {}
    for r in rows:
        sid = r.get("段号", "")
        if not sid:
            continue
        if sid not in seg_rows:
            seg_rows[sid] = []
            order.append(sid)
        seg_rows[sid].append(r)

    cues, offset = [], 0.0
    for sid in order:
        given, last_end = 0.0, 0.0
        for r in seg_rows[sid]:
            v = num(r.get("段时长", ""))
            if v and v > given:
                given = v
            rng = parse_range(r.get("时间区间", ""))
            if rng and rng[1] > last_end:
                last_end = rng[1]
            text = r.get("台词", "")
            if not text:
                continue
            if rng is None:
                warns.append(f"{sid}：时间区间 `{r.get('时间区间','')}` 解析不了，这句台词跳过")
                continue
            who = r.get("说话人", "")
            line = f"{who}：{text}" if speaker and who else text
            cues.append((rng[0] + offset, rng[1] + offset, line))
        offset += given if given > 0 else last_end
    return cues


def main():
    ap = argparse.ArgumentParser(description="分镜表 → SRT 字幕")
    ap.add_argument("--shots", required=True, help="漫剧分镜表或 MV 分镜表 CSV")
    ap.add_argument("--out", required=True, help="输出 .srt")
    ap.add_argument("--speaker", action="store_true", help="漫剧模式下每句前加「说话人：」")
    ap.add_argument("--offset", type=float, default=0.0, help="整体平移秒数")
    args = ap.parse_args()

    rows = read_csv(args.shots)
    if not rows:
        raise SystemExit("[错误] 分镜表是空的。")
    warns = []
    mv = "歌词" in rows[0] and "拍点时间" in rows[0]
    cues = cues_mv(rows, warns) if mv else cues_manju(rows, args.speaker, warns)
    cues.sort(key=lambda c: c[0])

    blocks = []
    for i, (s, e, text) in enumerate(cues, 1):
        blocks.append(f"{i}\n{srt_time(s + args.offset)} --> {srt_time(e + args.offset)}\n{text}\n")
    # utf-8-sig：PR / 剪映读中文 SRT 更稳
    with open(args.out, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write("\n".join(blocks))

    print(f"[OK] {'MV 歌词' if mv else '漫剧台词'}模式，导出 {len(cues)} 条字幕 → {args.out}")
    for w in warns:
        print(f"[!] {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
