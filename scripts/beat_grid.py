#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
beat_grid.py —— BPM / 拍号 → 小节时间表；按「小节」列自动填 MV 分镜表的「拍点时间」和「时长」。

用法：
    # 1. 打印小节时间表（BPM 120、4/4 拍、第 1 小节从 0.5 秒开始，算 64 小节）
    python beat_grid.py --bpm 120 --beats 4 --offset 0.5 --bars 64

    # 2. 按「小节」列填分镜表（拍点时间为空的行才填；已填但对不上的会警告）
    python beat_grid.py --bpm 120 --shots 01_脚本/01_MV分镜表.csv --out 01_脚本/01_MV分镜表.csv

    # 3. 导出小节号字幕：导进 PR 当字幕轨，时间线上直接看到第几小节、哪个段落
    python beat_grid.py --bpm 120 --shots 01_脚本/01_MV分镜表.csv --srt 01_小节.srt

参数：
    --bpm      曲速（必填）
    --beats    每小节几拍，默认 4（3/4 拍写 3，6/8 按附点四分音符算就写 2）
    --offset   第 1 小节第 1 拍在音频里的秒数，默认 0（前面有空白或弱起就填这里）
    --change   中途变速，格式「小节:BPM」，可多个：--change 33:140 49:120
    --bars     打印 / 导出多少小节，默认按分镜表最大小节号，没有分镜表就 32
    --force    分镜表里已填的拍点时间也按小节重算覆盖

「小节」列写法（含头含尾）：
    5          第 5 小节整小节
    1-2        第 1 到第 2 小节
    5.3        第 5 小节第 3 拍（一拍）
    5.1-5.2    第 5 小节第 1–2 拍
    7.3-8      第 7 小节第 3 拍到第 8 小节结束

退出码：0 = 没有错误，1 = 有错误（小节写法解析不了）。
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

TOL = 0.05


def read_csv(path):
    last = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                rows = [{(k or "").strip(): (v or "").strip() for k, v in r.items() if k} for r in reader]
                return [h.strip() for h in (reader.fieldnames or [])], rows
        except UnicodeDecodeError as e:
            last = e
    raise SystemExit(f"[错误] 读不了 {path}：{last}")


class Grid:
    """按小节号算绝对时间，支持中途变速。"""

    def __init__(self, bpm, beats, offset, changes):
        self.beats = beats
        tempo = {1: bpm}
        for c in changes:
            m = re.fullmatch(r"\s*(\d+)\s*[:：]\s*([\d.]+)\s*", c)
            if not m:
                raise SystemExit(f"[错误] --change `{c}` 写法错，应为「小节:BPM」，如 33:140")
            tempo[int(m.group(1))] = float(m.group(2))
        self.tempo = sorted(tempo.items())
        self.offset = offset
        self._starts = {1: offset}

    def bpm_at(self, bar):
        cur = self.tempo[0][1]
        for b, v in self.tempo:
            if b <= bar:
                cur = v
        return cur

    def bar_len(self, bar):
        return self.beats * 60.0 / self.bpm_at(bar)

    def bar_start(self, bar):
        if bar not in self._starts:
            last = max(b for b in self._starts if b < bar)
            t = self._starts[last]
            for b in range(last, bar):
                t += self.bar_len(b)
                self._starts[b + 1] = t
        return self._starts[bar]

    def pos(self, bar, beat, end):
        """小节.拍 → 秒。end=True 取这一拍（或整小节）的结尾。"""
        start = self.bar_start(bar)
        if beat is None:
            return start + (self.bar_len(bar) if end else 0)
        one = self.bar_len(bar) / self.beats
        return start + (beat if end else beat - 1) * one


def parse_pos(tok, beats):
    m = re.fullmatch(r"(\d+)(?:\.(\d+))?", tok)
    if not m:
        return None
    bar, beat = int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
    if bar < 1 or (beat is not None and not 1 <= beat <= beats):
        return None
    return bar, beat


def parse_bars(text, beats):
    t = (text or "").strip()
    for ch in ("～", "~", "—", "–", "－", "至"):
        t = t.replace(ch, "-")
    t = re.sub(r"\s+", "", t)
    if not t:
        return None
    parts = t.split("-")
    if len(parts) > 2:
        return None
    a = parse_pos(parts[0], beats)
    b = parse_pos(parts[-1], beats)
    if not a or not b or (b[0], b[1] or beats) < (a[0], a[1] or 1):
        return None
    return a, b


def to_sec(tok):
    """'3.8' / '0:03.8' -> 秒"""
    if ":" in tok:
        m, s = tok.split(":", 1)
        return int(m) * 60 + float(s)
    return float(tok)


def fmt1(sec):
    s = f"{sec:.2f}"
    s = s.rstrip("0")
    return s + "0" if s.endswith(".") else s


def srt_time(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    ap = argparse.ArgumentParser(description="BPM → 小节时间表 / 自动填 MV 分镜表拍点时间")
    ap.add_argument("--bpm", type=float, required=True, help="曲速")
    ap.add_argument("--beats", type=int, default=4, help="每小节几拍，默认 4")
    ap.add_argument("--offset", type=float, default=0.0, help="第 1 小节开始的秒数，默认 0")
    ap.add_argument("--change", nargs="*", default=[], help="中途变速：小节:BPM，可多个")
    ap.add_argument("--bars", type=int, default=0, help="打印 / 导出多少小节")
    ap.add_argument("--shots", default="", help="MV 分镜表 CSV（按「小节」列填拍点时间）")
    ap.add_argument("--out", default="", help="填好的分镜表写到哪（可以和 --shots 同一个文件）")
    ap.add_argument("--srt", default="", help="导出小节号字幕 SRT")
    ap.add_argument("--force", action="store_true", help="已填的拍点时间也按小节重算覆盖")
    args = ap.parse_args()

    if args.bpm <= 0 or args.beats <= 0:
        raise SystemExit("[错误] --bpm 和 --beats 必须大于 0")
    grid = Grid(args.bpm, args.beats, args.offset, args.change)

    errors, warns, filled = [], [], 0
    header, rows = [], []
    part_of_bar = {}
    max_bar = 0

    if args.shots:
        header, rows = read_csv(args.shots)
        if "小节" not in header:
            raise SystemExit("[错误] 分镜表没有「小节」列（列名见 assets/MV分镜表模板.csv）")
        for col in ("拍点时间", "时长"):
            if col not in header:
                header.append(col)
        for i, r in enumerate(rows, start=2):
            sid = r.get("段号", "") or f"第 {i} 行"
            raw = r.get("小节", "")
            if not raw:
                continue
            rng = parse_bars(raw, args.beats)
            if rng is None:
                errors.append(f"{sid}：小节 `{raw}` 写法错，应为 `5` / `1-2` / `5.3` / `7.3-8`（拍号 1–{args.beats}）")
                continue
            (b1, k1), (b2, k2) = rng
            start, end = grid.pos(b1, k1, False), grid.pos(b2, k2, True)
            max_bar = max(max_bar, b2)
            for b in range(b1, b2 + 1):
                part_of_bar.setdefault(b, r.get("歌曲段落", ""))
            new = f"{fmt1(start)}-{fmt1(end)}"
            old = r.get("拍点时间", "")
            if old and not args.force:
                m = re.fullmatch(r"\s*([\d.:]+)\s*[-～~—–]\s*([\d.:]+)\s*", old)
                try:
                    if abs(to_sec(m.group(1)) - start) > TOL or abs(to_sec(m.group(2)) - end) > TOL:
                        warns.append(f"{sid}：拍点时间写 `{old}`，按小节 {raw} 算是 `{new}`（加 --force 覆盖）")
                except (AttributeError, ValueError):
                    warns.append(f"{sid}：拍点时间 `{old}` 解析不了，按小节应为 `{new}`（加 --force 覆盖）")
                continue
            r["拍点时间"] = new
            r["时长"] = fmt1(end - start)
            filled += 1

    bars = args.bars or max_bar or 32

    out = [f"# 小节时间表 · {args.bpm:g} BPM · {args.beats} 拍 / 小节 · 起点 {args.offset:g}s", ""]
    if len(grid.tempo) > 1:
        out.append("变速：" + "，".join(f"第 {b} 小节起 {v:g} BPM" for b, v in grid.tempo[1:]))
        out.append("")
    out += ["| 小节 | 开始 | 结束 | BPM | 段落 |", "|---|---|---|---|---|"]
    for b in range(1, bars + 1):
        s = grid.bar_start(b)
        out.append(f"| {b} | {fmt1(s)} | {fmt1(s + grid.bar_len(b))} | {grid.bpm_at(b):g} | {part_of_bar.get(b, '')} |")
    if args.shots:
        out += ["", f"分镜表：填了 {filled} 行拍点时间"]
        out += [f"- ✗ {e}" for e in errors] + [f"- ⚠️ {w}" for w in warns]
    print("\n".join(out))

    if args.shots and args.out:
        with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"[OK] 分镜表已写入：{args.out}（下一步跑 check_mv_shots.py 体检）")

    if args.srt:
        lines = []
        for b in range(1, bars + 1):
            s = grid.bar_start(b)
            label = f"第{b}小节" + (f" · {part_of_bar[b]}" if part_of_bar.get(b) else "")
            lines += [str(b), f"{srt_time(s)} --> {srt_time(s + grid.bar_len(b))}", label, ""]
        with open(args.srt, "w", encoding="utf-8-sig", newline="\n") as f:
            f.write("\n".join(lines))
        print(f"[OK] 小节字幕已写入：{args.srt}（PR 里「文件 → 导入」当字幕轨，拼动态分镜时对拍用）")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
