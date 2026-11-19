#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长文朗读器 · 安装脚本

用法：
    python scripts/install.py                          # 装到 ~/TTSReader
    python scripts/install.py --target D:/Tools/TTSReader
    python scripts/install.py --mirror tsinghua        # 指定国内镜像
    python scripts/install.py --skip-test              # 跳过联网冒烟测试

设计目标：装到任何一台有 Python 的电脑上，一条命令跑通。
没有 Python 时给出明确指引，而不是抛一堆英文报错。
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MIN_PY = (3, 10)
COPY = ["app.py", "index.html", "start.bat", "start.sh", "README.md", "requirements.txt"]

MIRRORS = {
    "official": None,
    "tsinghua": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "aliyun": "https://mirrors.aliyun.com/pypi/simple/",
    "ustc": "https://mirrors.ustc.edu.cn/pypi/simple",
}


def info(msg):
    print(f"  {msg}")


def ensure_python():
    if sys.version_info < MIN_PY:
        print(f"\n[x] 需要 Python {MIN_PY[0]}.{MIN_PY[1]} 或更高版本。")
        print(f"    这台电脑上是 {sys.version.split()[0]}。")
        print("    下载地址：https://www.python.org/downloads/")
        print("    安装时务必勾选 “Add Python to PATH”。\n")
        sys.exit(1)
    info(f"Python {sys.version.split()[0]}  ✓")


def has_edge_tts():
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return False
    return True


def pip_once(mirror_url):
    req = os.path.join(ROOT, "requirements.txt")
    cmd = [sys.executable, "-m", "pip", "install", "-q", "-r", req]
    if mirror_url:
        cmd += ["-i", mirror_url, "--trusted-host", mirror_url.split("/")[2]]
    else:
        cmd += ["--timeout", "20"]
    return subprocess.call(cmd) == 0


def install_deps(mirror):
    if has_edge_tts():
        info("依赖 edge-tts 已存在，跳过安装  ✓")
        return True

    order = []
    if mirror and mirror in MIRRORS:
        order.append(mirror)
        order += [k for k in ("aliyun", "tsinghua", "official") if k != mirror]
    else:
        # 实测（2026-11-19）：阿里云镜像可用；清华镜像当时返回 no matching distribution
        order = ["aliyun", "tsinghua", "official"]

    for name in order:
        url = MIRRORS.get(name)
        label = "官方源" if name == "official" else f"{name} 镜像"
        info(f"尝试从{label}安装 edge-tts …")
        if pip_once(url):
            info("依赖就绪  ✓")
            return True
        info(f"{label}没成功，换下一个")

    print("  [!] 依赖安装全部失败。")
    print("      手动执行： pip install -i https://pypi.tuna.tsinghua.edu.cn/simple edge-tts")
    return False


def copy_files(target):
    os.makedirs(target, exist_ok=True)
    for name in COPY:
        src = os.path.join(ROOT, name)
        if not os.path.exists(src):
            continue
        shutil.copy2(src, os.path.join(target, name))
        info(f"已复制 {name}")
    bat = os.path.join(target, "start.bat")
    if os.path.exists(bat):
        data = open(bat, "rb").read()
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        with open(bat, "wb") as f:
            f.write(data)
        info("已规范化 start.bat 换行（CRLF）  ✓")


def smoke_test():
    try:
        import asyncio

        import edge_tts
    except ImportError:
        print("  [!] 未安装 edge-tts，跳过冒烟测试")
        return False

    async def _run():
        comm = edge_tts.Communicate(
            "朗读器安装完成，可以开始使用了。", "zh-CN-YunxiNeural"
        )
        total = 0
        async for msg in comm.stream():
            if msg["type"] == "audio":
                total += len(msg["data"])
        return total

    try:
        n = asyncio.run(_run())
    except Exception as e:  # noqa: BLE001
        print(f"  [!] 冒烟测试失败：{e}")
        print("      常见原因：网络不通、系统代理拦截、微软接口临时限流。")
        print("      可先跳过检查：--skip-test")
        return False
    if n <= 0:
        print("  [!] 服务返回空音频，稍后重试或检查网络")
        return False
    info(f"语音接口连通，合成 {n} 字节音频  ✓")
    return True


def main():
    ap = argparse.ArgumentParser(description="长文朗读器安装脚本")
    ap.add_argument(
        "--target",
        default=os.path.join(os.path.expanduser("~"), "TTSReader"),
        help="安装目录，默认 ~/TTSReader",
    )
    ap.add_argument(
        "--mirror",
        choices=["auto", "official", "tsinghua", "aliyun", "ustc"],
        default="auto",
        help="pip 源，默认 auto（官方不通就自动换国内镜像）",
    )
    ap.add_argument("--skip-deps", action="store_true", help="跳过 pip 安装")
    ap.add_argument("--skip-test", action="store_true", help="跳过联网冒烟测试")
    args = ap.parse_args()
    target = os.path.abspath(args.target)

    print("\n长文朗读器 · 安装\n" + "-" * 46)
    ensure_python()

    ok = True
    if args.skip_deps:
        info("已跳过依赖安装")
    else:
        ok = install_deps(None if args.mirror == "auto" else args.mirror)

    copy_files(target)

    if args.skip_test:
        info("已跳过冒烟测试")
    else:
        smoke_test()

    print("-" * 46)
    print(f"安装目录：{target}")
    if not ok:
        print("状态：文件已就位，但依赖未装好，需要手动执行上面那条 pip 命令。")
    print("\n下一步：")
    if os.name == "nt":
        print(f"  双击 {os.path.join(target, 'start.bat')}")
        print(f"  或在命令行： cd \"{target}\" 然后 python app.py")
    else:
        print(f'  cd "{target}" && bash start.sh')
    print("  浏览器会自动打开 http://127.0.0.1:8731/\n")


if __name__ == "__main__":
    main()
