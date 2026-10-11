#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_selftest.py —— 一条命令跑完所有脚本的回归测试。改完脚本先跑它。

用法：
    python scripts/run_selftest.py

GitHub Actions 每次推送也会自动跑（.github/workflows/selftest.yml）。
全部通过退出码 0，有一项失败退出码 1。
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
S = ROOT / "scripts"
T = ROOT / "_selftest"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

passed, failed = 0, []


def run(script, *args):
    p = subprocess.run([sys.executable, str(S / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8", env=ENV, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed.append(name)
        print(f"  ✗ {name}")
        if detail:
            print("    " + detail.strip().replace("\n", "\n    ")[:2000])


def main():
    tmp = Path(tempfile.mkdtemp(prefix="manju_selftest_"))

    print("build_prompts.py")
    out = tmp / "ok.md"
    code, log = run("build_prompts.py", "--shots", T / "示例_分镜表.csv", "--assets", T / "示例_素材编号表.csv", "--out", out)
    text = out.read_text(encoding="utf-8") if out.exists() else ""
    check("正常样本：校验通过", code == 0 and "校验通过" in log, log)
    check("正常样本：每条生成都有上传清单", text.count("**上传清单**") == 2, text[:800])
    check("两层编号：编号表图片6 → 槽位 @图片2", "| 2 | @图片2 | 图片6 |" in text and "@图片2用于婚礼彩排晚宴厅" in text, text[:1500])
    check("两层编号：音色排在图片后面", "| 6 | @音频1 |" in text or "| 6 | @音频2 |" in text, text[:1500])
    check("合并段的两段概述都保留", "随后，林晚说完宣战台词后转身离场" in text, text[:1500])

    code, log = run("build_prompts.py", "--shots", T / "示例_分镜表_坏数据.csv", "--assets", T / "示例_素材编号表.csv", "--out", tmp / "bad.md")
    check("坏数据：报 15 个问题（段时长写「6秒」也不崩）", code == 0 and "校验发现 15 个问题" in log, log)
    for key in ("人物2", "混了 中文", "没写说话人", "没挂 TA 的音色"):
        check(f"坏数据：抓到「{key}」", key in log, log)

    code, log = run("build_prompts.py", "--shots", T / "示例_分镜表.csv", "--assets", T / "示例_素材编号表.csv", "--ratio", "16:9")
    check("--ratio 16:9 时守则写横屏", "16:9 横屏构图" in log and "16:9 竖屏" not in log, log[:600])

    long_csv = tmp / "long.csv"
    long_csv.write_text(
        "段号,时间区间,段时长,段概述,画面内容,参考素材\n"
        f"seg01,0s-8s,8,超长提示词测试,{'林晚站在长桌旁看着窗外的雨' * 120},@图片1\n",
        encoding="utf-8")
    code, log = run("build_prompts.py", "--shots", long_csv, "--assets", T / "示例_素材编号表.csv", "--out", tmp / "long.md")
    check("提示词超过 1500 字会报", "超过 1500 字" in log, log)

    print("check_consistency.py")
    code, log = run("check_consistency.py", "--project", "_selftest/多集测试")
    check("多集测试：4 错 7 警 2 提示，退出码 1", code == 1 and "有 4 个错误需要修（7 个警告 / 2 条提示）" in log, log[-1500:])
    check("多集测试：抓到剧情状态接不上", "剧情状态接不上" in log, log[-1500:])
    check("多集测试：报告不暴露本机绝对路径", str(ROOT) not in log, "")
    code, log = run("check_consistency.py", "--project", T / "干净项目")
    check("干净项目：通过，退出码 0", code == 0 and "✓ 通过（0 个警告" in log, log[-800:])

    print("check_mv_shots.py")
    code, log = run("check_mv_shots.py", "--shots", T / "MV测试" / "正常_MV分镜表.csv")
    check("正常 MV 分镜表：通过", code == 0 and "✓ 通过（0 个警告 / 0 条提示）" in log, log[-800:])
    code, log = run("check_mv_shots.py", "--shots", T / "MV测试" / "坏_MV分镜表.csv")
    check("坏 MV 分镜表：3 错 4 警 2 提示，退出码 1", code == 1 and "有 3 个错误需要修（4 个警告 / 2 条提示）" in log, log[-1500:])

    print("beat_grid.py")
    filled, bars = tmp / "bars.csv", tmp / "bars.srt"
    code, log = run("beat_grid.py", "--bpm", 120, "--offset", 0.5, "--change", "7:150",
                    "--shots", T / "MV测试" / "小节_MV分镜表.csv", "--out", filled, "--srt", bars)
    body = filled.read_text(encoding="utf-8-sig") if filled.exists() else ""
    check("按小节填拍点时间：填 5 行、拍级写法也能算", "填了 5 行" in log
          and "s01,前奏,1-2,0.5-4.5,4.0" in body and "s04,主歌A,5.3-6,9.5-12.5,3.0" in body, log + body)
    check("中途变速：第 7 小节起 150 BPM", "s05,副歌,7-8,12.5-15.7,3.2" in body, body)
    check("已填但对不上的报警告、写反的小节报错，退出码 1",
          code == 1 and "按小节 9 算是 `15.7-17.3`" in log and "小节 `10-9` 写法错" in log, log)
    srt = bars.read_text(encoding="utf-8-sig") if bars.exists() else ""
    check("小节字幕带段落名", "00:00:12,500 --> 00:00:14,100\n第7小节 · 副歌" in srt, srt[:600])
    code, log = run("beat_grid.py", "--bpm", 120, "--offset", 0.5, "--change", "7:150", "--force",
                    "--shots", T / "MV测试" / "小节_MV分镜表.csv", "--out", filled)
    body = filled.read_text(encoding="utf-8-sig") if filled.exists() else ""
    check("--force 覆盖已填的拍点时间", "s06,副歌,9,15.7-17.3,1.6" in body, body)

    print("make_srt.py")
    srt = tmp / "mv.srt"
    code, log = run("make_srt.py", "--shots", T / "MV测试" / "正常_MV分镜表.csv", "--out", srt)
    body = srt.read_text(encoding="utf-8-sig") if srt.exists() else ""
    check("MV 歌词导出 3 条、双语分行", "导出 3 条字幕" in log and "I get to see a smile again\n又能看见你的笑容" in body, log + body[:300])
    srt = tmp / "ep.srt"
    code, log = run("make_srt.py", "--shots", T / "示例_分镜表.csv", "--out", srt, "--speaker")
    body = srt.read_text(encoding="utf-8-sig") if srt.exists() else ""
    check("漫剧台词按段累加时间轴", "00:00:13,000 --> 00:00:16,000\n林晚：这地方" in body, body)

    print("log_summary.py")
    code, log = run("log_summary.py", "--log", T / "生成日志示例.csv")
    check("汇总：8 次生成、平均抽 2.3 次、1 个镜头未过",
          "生成 8 次，涉及 4 个镜头" in log and "平均抽 **2.3** 次" in log and "EP01/seg02" in log, log[:1200])
    check("按镜头类型算每个镜头平均耗时（报价用）", "| 1 | 1 | 1 / 0 / 0 | 100% | 1.0 | 1.5h | 90 分钟 | 0 |" in log, log[-1500:])

    print("init_project.py")
    code, log = run("init_project.py", "--type", "mv", "--name", "自测", "--dir", tmp, "--role", "角色A")
    proj = tmp / "同人MV_自测"
    check("MV 项目骨架建好", code == 0 and (proj / "00_企划/风格圣经.md").is_file()
          and (proj / "00_企划/设定考据_角色A.md").is_file() and (proj / "02_资产/角色/角色A").is_dir(), log)
    (proj / "00_企划/风格圣经.md").write_text("我改过", encoding="utf-8")
    code, log = run("init_project.py", "--type", "mv", "--name", "自测", "--dir", tmp)
    check("重复运行不覆盖已有文件", (proj / "00_企划/风格圣经.md").read_text(encoding="utf-8") == "我改过", log)
    code, log = run("init_project.py", "--type", "manju", "--name", "自测", "--dir", tmp, "--role", "林晚")
    proj = tmp / "漫剧项目_自测"
    check("漫剧项目骨架建好（含剧集大纲、角色圣经）", code == 0 and (proj / "01_剧本/第01集_分镜表.csv").is_file()
          and (proj / "01_剧本/剧集大纲.md").is_file() and (proj / "02_资产/角色/林晚/林晚_角色圣经.md").is_file(), log)

    print()
    if failed:
        print(f"✗ {len(failed)} 项失败 / {passed} 项通过")
        return 1
    print(f"✓ 全部 {passed} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
