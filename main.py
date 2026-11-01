"""入口：初始化客户端 → 查询状态 → 定位校验 → 打卡 → 推送结果。

用法：
    python main.py                 # 正常跑（21:35–23:59 之外自动静默退出）
    python main.py --doctor        # 自检：只读，不打卡，随时可跑
    python main.py --force         # 忽略时间窗，用来测试（真会打卡，慎用）
    python main.py --token xxx     # 临时用指定 token

v1.1 改动：
- 区分「token 失效（服务端 500 请先登录）」与「服务器暂未返回计划」，
  不再把登录失效误报成「没计划」。
- 适配 2026-11 新版 H5：是否已签看 statusData.status，有无计划看 FPlanID。
- 新增 --doctor 自检，几秒钟就能判断 token / 计划 / 坐标是否正常。
"""
import argparse
import datetime
import json
import re

import requests

from src.checkin import (
    ApiError,
    AttnClient,
    TokenInvalid,
    beijing_now,
    campus_list,
    collect_polygons,
    do_checkin,
    query_today_task,
)
from src.config import load_config
from src.notify import notify
from src.vacation import matched_range, today_cn

# 晚点名窗口 21:30-23:59；21:30-21:34 到达的运行等满 21:35 再签，避开刚开窗的边界
WINDOW_START = datetime.time(21, 30)
SUBMIT_FROM = datetime.time(21, 35)
WINDOW_END = datetime.time(23, 59, 59)
FIRST_RUN_UNTIL = datetime.time(22, 0)  # 这之前算首跑，允许推送一次提醒


def _save_token(cfg, token: str):
    """把新 token 回写到 config.yaml（避免每次运行都重新登录）。"""
    try:
        path = cfg.get("_config_path")
        if not path:
            return
        text = open(path, encoding="utf-8").read()
        new_text, n = re.subn(
            r'(token:\s*")([^"]*)(")', rf"\g<1>{token}\g<3>", text, count=1
        )
        if n:
            open(path, "w", encoding="utf-8").write(new_text)
            print("新 token 已回写 config.yaml")
    except Exception as e:
        print(f"token 回写失败（不影响本次运行）: {e}")


def _mask(token: str) -> str:
    if not token:
        return "（空）"
    if len(token) <= 12:
        return token[:2] + "***"
    return f"{token[:6]}…{token[-4:]}（{len(token)} 位）"


def _resolve_token(cfg) -> str:
    """优先用配置里的 token；没有才走学号密码登录。"""
    token = (cfg.get("user") or {}).get("token", "")
    if token:
        return token
    username = (cfg.get("user") or {}).get("username", "")
    password = (cfg.get("user") or {}).get("password", "")
    if not (username and password):
        raise SystemExit("config.yaml 未配置 token，且学号/密码不全。")

    from src.login import LoginNeedsSms, login as sso_login

    try:
        token = sso_login(username, password)
    except LoginNeedsSms as e:
        raise SystemExit(
            f"学号密码登录被短信二次验证拦住：{e}\n"
            "→ 请运行 python tools/token_tool.py grab（从模拟器/手机抓 token）"
        )
    print("SSO 登录成功，已获取新 token")
    _save_token(cfg, token)
    return token


def _refresh_from_device(cfg) -> str:
    """可选：token 失效时，从模拟器里已登录的 App 抓一个最新的。"""
    dev = cfg.get("device") or {}
    if not dev.get("auto_refresh"):
        return ""
    try:
        from tools.token_tool import grab_token

        token = grab_token(dev.get("adb_path"), dev.get("package"))
        if token:
            cfg.setdefault("user", {})["token"] = token
            _save_token(cfg, token)
            print("已从设备抓取到新 token")
        return token or ""
    except Exception as e:
        print(f"从设备抓 token 失败：{e}")
        return ""


def _account_id(token: str) -> str:
    return _mask(token)


def doctor(cfg) -> int:
    """体检：只读接口，判断 token / 计划 / 坐标。"""
    now = beijing_now()
    print("=" * 64)
    print("晚点名自检  ", f"{now:%Y-%m-%d %H:%M:%S}（北京时间）")
    print("=" * 64)

    vac = matched_range(cfg)
    print(f"[1/4] 假期规则：{'命中 ' + vac + '，今晚会跳过' if vac else '未命中，会正常签到'}"
          f"（今天 {today_cn()}）")

    token = _resolve_token(cfg)
    print(f"[2/4] 登录凭证：{_account_id(token)}")

    client = AttnClient(token)
    try:
        status = query_today_task(client, cfg)
    except TokenInvalid as e:
        print(f"      接口结果：❌ token 失效 —— {e}")
        print()
        print("结论：token 已经过期/被服务端作废。这不是「没有计划」。")
        print("      解决：python tools/token_tool.py grab")
        print("      （前提：模拟器里已登录智汇福大，并打开过一次晚点名签到页）")
        return 2
    except requests.HTTPError as e:
        print(f"      接口结果：❌ 服务器异常 {e}")
        return 3
    except ApiError as e:
        print(f"      接口结果：❌ {e}")
        return 3
    except Exception as e:
        print(f"      接口结果：❌ {e}")
        return 3

    data = status["init"]
    params = data.get("paramsData") or {}
    user = data.get("userData") or {}
    st = (data.get("statusData") or {}).get("status")
    print("      接口结果：✅ init.action 正常")
    print(f"      计划：FPlanID={user.get('FPlanID') or '（空）'}"
          f"  时段 {params.get('FStartTime') or '?'}-{params.get('FEndTime') or '?'}"
          f"  迟到线 {params.get('FLateTime') or '?'}")
    print(f"      状态：statusData.status={st or '（空）'}"
          f"  FWay={user.get('FWay')}  FState={user.get('FState')}")
    print(f"      签到范围：{len(collect_polygons(data))} 个多边形；"
          f"校区 {campus_list(data) or '（空）'}")
    print(f"      免签原因：{user.get('FReason') or '—'}")

    lon = cfg["checkin"]["longitude"]
    lat = cfg["checkin"]["latitude"]
    print(f"[3/4] 配置坐标：{lon}, {lat}  文案：{cfg['checkin'].get('actual_location') or '—'}")

    if status["reason"] == "no_plan":
        print("[4/4] 定位校验：跳过（服务器今天没下发计划）")
        print()
        print("结论：接口和 token 都正常，但今天没有晚点名计划。")
        print("      如果现在已经是晚上 21:30 之后，说明当晚确实不用打卡；")
        print("      如果全宿舍都要打而你这里空着，请把上面的输出发给开发者。")
        return 0

    polygons = collect_polygons(data)
    if not polygons:
        print("[4/4] 定位校验：跳过（计划里没有签到范围数据）")
        return 0
    try:
        matched = client.check(float(lon), float(lat), {"range": polygons})
    except Exception as e:
        print(f"[4/4] 定位校验：❌ {e}")
        return 3

    if matched:
        first = matched[0] if isinstance(matched[0], dict) else {}
        print(f"[4/4] 定位校验：✅ 命中「{first.get('title') or first.get('name') or first.get('id')}」")
        print()
        if status["reason"] == "already":
            print("结论：今日已经签到了（statusData.status=SUCCESS），今晚不会再提交。")
        elif status["reason"] == "need_not":
            print("结论：今天无需签到（FWay=3）。")
        else:
            print("结论：一切正常，今晚 21:35 后会自动签到。")
        return 0

    print("[4/4] 定位校验：❌ 不在任何签到范围内")
    print()
    print("结论：token 和计划都正常，是坐标没落在签到多边形里。")
    print("      请用高德坐标拾取器重新取你所在楼栋的经纬度（GCJ-02），改 config.yaml。")
    return 4


def run(cfg) -> int:
    if cfg.get("_force"):
        print("⚠️ --force：忽略时间窗限制")
    else:
        now = beijing_now()
        if WINDOW_START <= now.time() < SUBMIT_FROM:
            wait = (datetime.datetime.combine(now.date(), SUBMIT_FROM) - now).total_seconds()
            print(f"当前北京时间 {now:%H:%M}，等待 {int(wait) + 1} 秒到 21:35 再执行")
            import time as _t

            _t.sleep(max(wait, 0) + 1)
            now = beijing_now()
        if not (SUBMIT_FROM <= now.time() <= WINDOW_END):
            print(f"北京时间 {now:%H:%M} 不在晚点名窗口（21:35-23:59），判定为延迟触发的补跑，静默跳过。")
            return 0

    vac = matched_range(cfg)
    if vac:
        print(f"假期中（{vac}），跳过本次签到。今天是 {today_cn()}。")
        if (cfg.get("vacation") or {}).get("notify"):
            notify(cfg, f"智汇福大晚点名：假期中，已跳过（{vac}）", "假期期间自动签到暂停。")
        return 0

    token = _resolve_token(cfg)
    client = AttnClient(token)

    try:
        status = query_today_task(client, cfg)
    except TokenInvalid as e:
        # token 失效：先试设备抓取，再决定是否推送
        new_token = _refresh_from_device(cfg)
        if new_token:
            client = AttnClient(new_token)
            try:
                status = query_today_task(client, cfg)
            except Exception as e2:
                print("换新 token 后仍然失败：", e2)
                notify(cfg, "智汇福大晚点名：登录凭证失效 ❌",
                       f"token 已被服务器拒绝（{e}），自动刷新也没能恢复，请手动处理。")
                return 2
        else:
            title = "智汇福大晚点名：登录凭证已失效 ❌"
            print(title, e)
            if beijing_now().time() < FIRST_RUN_UNTIL:
                notify(cfg, title,
                       f"token 被服务器拒绝：{e}\n"
                       "这不是「没有计划」。请运行 python tools/token_tool.py grab 更新 token，"
                       "或重新用 App 打开一次晚点名页面。")
            else:
                print("（兜底跑：token 失效，静默跳过，不再重复推送）")
            return 2
    except (requests.HTTPError, ApiError) as e:
        title = "智汇福大晚点名：接口异常，本次跳过"
        print(title, e)
        if beijing_now().time() < FIRST_RUN_UNTIL:
            notify(cfg, title, f"接口返回异常：{e}")
        return 1

    init_data = status["init"]

    if status["reason"] == "already":
        print("今日已签到 / 处理中，无需操作（不推送）")
        return 0
    if status["reason"] == "need_not":
        print("今日无需签到（ClockWay=3），不推送")
        return 0
    if status["reason"] == "no_plan":
        title = "智汇福大晚点名：今日无考勤计划"
        print(title)
        if beijing_now().time() < FIRST_RUN_UNTIL:
            notify(cfg, title, "服务器未下发晚点名计划（FPlanID 为空），未执行打卡。")
        else:
            print("（晚间兜底跑：无考勤计划，静默跳过，不重复推送）")
        return 0

    try:
        result = do_checkin(client, cfg, init_data, dry_run=bool(cfg.get("_dry_run")))
    except TokenInvalid as e:
        notify(cfg, "智汇福大晚点名：登录凭证失效 ❌", f"打卡阶段 token 被拒绝：{e}")
        return 2
    except Exception as e:  # 接口报错（如不在时段/范围）要带原因推送
        title = "智汇福大晚点名：签到失败 ❌"
        print(title, e)
        notify(cfg, title, f"自动签到失败：{e}\n请手动打开 App 签到。")
        return 1

    if result["reason"] == "dry_run":
        print("dry-run：不提交。实际会发送的打卡参数：")
        print(json.dumps(result["payload"], ensure_ascii=False, indent=2))
        print(f"（命中校区 {result['campus']}，{'迟到' if result['late'] else '正常'}）")
        return 0

    if not result["ok"]:
        if result["reason"] == "no_range":
            title = "智汇福大晚点名：计划缺少签到范围 ❌"
            print(title)
            notify(cfg, title, "服务器没返回签到多边形，无法校验定位，未打卡。")
        else:
            title = "智汇福大晚点名：不在签到范围 ❌"
            print(title)
            notify(cfg, title, "定位校验未命中任何校区，请确认 config.yaml 里的经纬度。")
        return 4

    title = "智汇福大晚点名：签到成功 ✅"
    print(title, "| 校区:", result.get("campus"), "| 迟到:", result.get("late"))
    notify(cfg, title, f"已自动签到（{'迟到' if result.get('late') else '正常'}）。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="智汇福大晚点名自动签到")
    ap.add_argument("--doctor", action="store_true", help="只体检，不打卡")
    ap.add_argument("--force", action="store_true", help="忽略时间窗（会真的打卡）")
    ap.add_argument("--dry-run", action="store_true", help="只算打卡参数，不真正提交")
    ap.add_argument("--token", default="", help="临时指定 token")
    args = ap.parse_args()

    cfg = load_config()
    if args.token:
        cfg.setdefault("user", {})["token"] = args.token
    cfg["_force"] = args.force
    cfg["_dry_run"] = args.dry_run

    return doctor(cfg) if args.doctor else run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
