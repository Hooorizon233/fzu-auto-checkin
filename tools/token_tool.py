"""从安卓模拟器/手机里抓取「晚点名」免登 token，并写回 config.yaml。

背景：学校 2026-11 起对校外 IP 的学号密码登录强制短信二次验证，
无人值守的 SSO 登录走不通了。但智汇福大 App 里登录态是长期的，
只要在 App 里打开过「晚点名签到」页，H5 的完整 URL（含 token）就会落在
WebView 缓存里，用 adb 就能读出来。

用法：
    python tools/token_tool.py grab        # 抓最新可用 token 并写回 config.yaml
    python tools/token_tool.py check       # 只检查 config.yaml 里的 token 是否还有效
    python tools/token_tool.py list        # 列出设备上缓存的所有候选 token（不联网）

前提：
    1. 模拟器/手机已开启 USB 调试，adb 能连上（adb devices 能看到设备）；
    2. 智汇福大 App 已登录，并且打开过一次「业务 → 晚点名签到」页面；
    3. 设备可 root（雷电/MuMu 默认可以；真机需要 root 才能读 /data/data）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

import requests
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ADB = r"D:\leidian\LDPlayer9\adb.exe"
DEFAULT_PKG = "cn.edu.fzu.fdxypa"
CONFIG_PATH = os.path.join(ROOT, "config.yaml")

TOKEN_RE = re.compile(r"index\.action\?token=([A-Za-z0-9]{20,})")
INIT_URL = "https://yzsxg.fzu.edu.cn/livecloud/project/fzu/attn/init.action"
UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
      "Chrome/120 Mobile Safari/537.36")


def _load_cfg() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _adb_path(cli_value: str) -> str:
    if cli_value:
        return cli_value
    cfg = _load_cfg()
    dev = (cfg.get("device") or {})
    return dev.get("adb_path") or os.environ.get("ADB_PATH") or DEFAULT_ADB


def _pkg(cli_value: str) -> str:
    if cli_value:
        return cli_value
    return (_load_cfg().get("device") or {}).get("package") or DEFAULT_PKG


def adb(adb_path: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [adb_path, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _ensure_device(adb_path: str) -> None:
    if not os.path.exists(adb_path):
        sys.exit(f"找不到 adb：{adb_path}\n请用 --adb 指定，或在 config.yaml 的 device.adb_path 里配置。")
    r = adb(adb_path, "devices")
    out = (r.stdout or "") + (r.stderr or "")
    if "device" not in out.replace("List of devices attached", ""):
        sys.exit("adb 没有检测到设备。请先启动模拟器/连接手机，并开启 USB 调试。")
    # 读 /data/data 需要 root；雷电/MuMu 一般可以直接 adb root
    adb(adb_path, "root", timeout=30)
    adb(adb_path, "wait-for-device", timeout=30)


def collect_tokens(adb_path: str, pkg: str) -> list[str]:
    """从 WebView 缓存里抓所有出现过的 token（去重）。"""
    target = f"/data/data/{pkg}/cache/WebView"
    cmd = (
        'grep -a -rhoE "index[.]action[?]token=[A-Za-z0-9]+" '
        f'{target} 2>/dev/null'
    )
    r = adb(adb_path, "shell", cmd, timeout=120)
    seen, out = set(), []
    for m in TOKEN_RE.finditer((r.stdout or "") + (r.stderr or "")):
        tok = m.group(1)
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def validate(token: str) -> tuple[bool, str]:
    """用 init.action 验证 token 是否可用（只读，不会打卡）。"""
    try:
        r = requests.post(
            INIT_URL, json={}, timeout=20,
            headers={"User-Agent": UA, "token": token},
        )
    except requests.RequestException as e:
        return False, f"网络错误：{e}"
    if r.status_code >= 400:
        m = re.search(r'ext-mb-text[^>]*>(.*?)</div>', r.text, re.S)
        msg = re.sub(r"\s+", " ", m.group(1)).strip() if m else r.text[:80]
        return False, f"HTTP {r.status_code} {msg}"
    try:
        body = r.json()
    except ValueError:
        return False, "返回不是 JSON"
    if body.get("success") and body.get("code") == 1:
        return True, "ok"
    return False, str(body.get("msg") or body)[:80]


def grab_token(adb_path: str = "", pkg: str = "") -> str:
    """抓一个当前可用的 token（给 main.py 的自动刷新用）。失败返回空串。"""
    adb_path, pkg = _adb_path(adb_path), _pkg(pkg)
    try:
        _ensure_device(adb_path)
        for tok in collect_tokens(adb_path, pkg):
            if validate(tok)[0]:
                return tok
    except SystemExit:
        return ""
    except Exception:
        return ""
    return ""


def write_token(token: str) -> None:
    if not os.path.exists(CONFIG_PATH):
        sys.exit("没有 config.yaml，先 cp config.example.yaml config.yaml 并填写。")
    text = open(CONFIG_PATH, encoding="utf-8").read()
    new_text, n = re.subn(r'(^\s*token:\s*")([^"]*)(")', rf"\g<1>{token}\g<3>", text, count=1, flags=re.M)
    if not n:
        sys.exit("config.yaml 里没找到 token: \"...\" 这一行，请手动粘贴。")
    open(CONFIG_PATH, "w", encoding="utf-8").write(new_text)


def main() -> int:
    ap = argparse.ArgumentParser(description="从设备抓取晚点名 token")
    ap.add_argument("action", choices=["grab", "check", "list"], nargs="?", default="grab")
    ap.add_argument("--adb", default="", help="adb 可执行文件路径")
    ap.add_argument("--package", default="", help="智汇福大包名，默认 cn.edu.fzu.fdxypa")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写回 config.yaml")
    args = ap.parse_args()

    adb_path, pkg = _adb_path(args.adb), _pkg(args.package)

    if args.action == "check":
        token = ((_load_cfg().get("user") or {}).get("token") or "").strip()
        if not token:
            print("config.yaml 里没有 token")
            return 1
        ok, msg = validate(token)
        print(("✅ token 有效" if ok else "❌ token 无效") + f"：{msg}")
        print(f"   当前值：{token[:6]}…{token[-4:]}（{len(token)} 位）")
        return 0 if ok else 2

    _ensure_device(adb_path)
    tokens = collect_tokens(adb_path, pkg)
    print(f"设备里找到 {len(tokens)} 个候选 token（按出现顺序）：")
    for t in tokens:
        print(f"  - {t[:6]}…{t[-4:]}")
    if args.action == "list":
        return 0
    if not tokens:
        print("\n没有找到任何 token。请先在 App 里打开「业务 → 晚点名签到」页面，再重试。")
        return 1

    print("\n逐个验证（只调用 init.action，不会打卡）…")
    for t in tokens:
        ok, msg = validate(t)
        print(f"  {t[:6]}…{t[-4:]}  {'✅ 可用' if ok else '❌ ' + msg}")
        if ok:
            if args.dry_run:
                print("\n--dry-run：不写回。token =", t)
            else:
                write_token(t)
                print("\n✅ 已写回 config.yaml；新 token =", t)
                print("   下一步：python main.py --doctor")
            return 0

    print("\n所有候选 token 都失效了。请在 App 里重新打开一次「晚点名签到」页面，然后再运行本命令。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
