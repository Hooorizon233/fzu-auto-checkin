"""晚点名签到客户端（v1.1 · 适配 2026-11 新版 H5）。

协议逆向自智汇福大 App 晚点名 H5（yzsxg.fzu.edu.cn），完整协议见 docs/protocol.md。

要点：
- 认证：H5 页面 URL 的 `?token=xxx`（App 免登下发）→ 请求头 `token: <token>`
- 加密：AES-128-CBC，KEY = IV = "apexinfoapexinfo"，PKCS7，输出 Base64
- check / clockIn / currentTimestamp 为 GET，密文放查询参数 `param=<urlencode(密文)>`
- init 为 POST，明文 JSON

2026-11 新版 H5（Vite/React 重写）的响应字段变化：
- 是否已签：`statusData.status` ∈ {SUCCESS, PROCESSING, FAILED}（旧版看 userData.FState）
- 有无计划：`userData.FPlanID` 非空
- 迟到线：`paramsData.FLateTime`（如 23:00），迟到判定以服务器时间 currentTimestamp 为准
- 签到范围：`schoolData[].FPosition`（JSON 字符串，含 range 多边形列表）
"""

import base64
import datetime
import json
import re
import urllib.parse

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

BASE_URL = "https://yzsxg.fzu.edu.cn"
API_PREFIX = "/livecloud/project/fzu/attn"
AES_KEY = b"apexinfoapexinfo"  # KEY == IV（前端源码硬编码）

# GitHub Actions 跑在 UTC，迟到判定必须按北京时间算，不能用运行环境本地时间
TZ_CN = datetime.timezone(datetime.timedelta(hours=8), "Asia/Shanghai")

# 已签 / 处理中，都算“不用再签”
DONE_STATUSES = {"SUCCESS", "PROCESSING"}


def beijing_now(ts_ms=None) -> datetime.datetime:
    """服务器时间戳(毫秒) → 北京时间；没有则取当前北京时间。"""
    if ts_ms:
        return datetime.datetime.fromtimestamp(ts_ms / 1000, TZ_CN)
    return datetime.datetime.now(TZ_CN)


UA = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
    "Chrome/120 Mobile Safari/537.36"
)


class TokenInvalid(RuntimeError):
    """token 缺失/过期/被服务端拒绝（服务端返回 500 且提示「请先登录！」）。"""


class ApiError(RuntimeError):
    """接口返回了业务错误或异常状态码。"""


# ============ 加解密 ============


def encrypt(obj) -> str:
    """AES-128-CBC(key=iv) PKCS7 → Base64，与前端 Encrypt() 一致。"""
    data = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))
    cipher = AES.new(AES_KEY, AES.MODE_CBC, iv=AES_KEY)
    return base64.b64encode(cipher.encrypt(pad(data.encode(), AES.block_size))).decode()


def decrypt(b64text: str) -> str:
    """与前端 Decrypt() 一致：Base64 → AES-128-CBC 解密。"""
    cipher = AES.new(AES_KEY, AES.MODE_CBC, iv=AES_KEY)
    return unpad(cipher.decrypt(base64.b64decode(b64text)), AES.block_size).decode()


def error_text(resp: requests.Response, limit: int = 220) -> str:
    """从错误响应里尽量提取人能看懂的一句话。

    服务端出错时返回的是 Spring 的「信息提示」HTML 页，
    真正的原因在 .ext-mb-text 里（例如「请先登录！」）。
    """
    m = re.search(r'ext-mb-text[^>]*>(.*?)</div>', resp.text or "", re.S)
    if m:
        raw = re.sub(r"<[^>]+>", " ", m.group(1))
        return re.sub(r"\s+", " ", raw).strip()[:limit]
    try:
        body = resp.json()
        if isinstance(body, dict) and body.get("msg"):
            return str(body["msg"])[:limit]
    except Exception:
        pass
    return re.sub(r"\s+", " ", resp.text or "")[:limit]


# ============ 客户端 ============


class AttnClient:
    """晚点名接口客户端，token 来自 App 免登 URL（获取方式见 docs/protocol.md）。"""

    def __init__(self, token: str):
        self.token = (token or "").strip()
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": UA, "token": self.token})

    # ---- 底层请求 ----

    def _handle(self, resp: requests.Response, path: str):
        """统一处理：HTTP 状态 → 业务 code → data。"""
        if resp.status_code >= 400:
            msg = error_text(resp)
            if "请先登录" in msg or "未登录" in msg or "登录已失效" in msg:
                raise TokenInvalid(msg or "token 无效")
            raise ApiError(f"{path} HTTP {resp.status_code}: {msg}")
        try:
            body = resp.json()
        except ValueError:
            raise ApiError(f"{path} 返回的不是 JSON：{error_text(resp, 120)}")
        if not (body.get("success") and body.get("code") == 1):
            msg = str(body.get("msg") or body)[:220]
            if "请先登录" in msg:
                raise TokenInvalid(msg)
            raise ApiError(f"{path} 失败: {msg}")
        return body.get("data")

    def _enc_get(self, path: str, payload: dict):
        """GET，密文放 param 查询参数（对应前端 createEncryptApi）。

        注意：前端 axios 会对已 encodeURIComponent 的值再编码一次（双重编码），
        服务端自行 URLDecoder 解一层，这里保持一致。
        """
        param = urllib.parse.quote(
            urllib.parse.quote(encrypt(payload), safe=""), safe=""
        )
        url = f"{BASE_URL}{API_PREFIX}/{path}?param={param}"
        resp = self.http.get(url, timeout=15)
        return self._handle(resp, path)

    # ---- 接口 ----

    def init(self) -> dict:
        """查询今日晚点名计划与状态（POST，明文，对应前端 createCommonApi）。"""
        url = f"{BASE_URL}{API_PREFIX}/init.action"
        resp = self.http.post(url, json={}, timeout=15)
        return self._handle(resp, "init") or {}

    def server_time(self):
        """服务器时间戳（毫秒），用于迟到判定与本地时间校准。"""
        try:
            data = self._enc_get("currentTimestamp.action", {})
            return (data or {}).get("timestamp")
        except TokenInvalid:
            raise
        except Exception:
            return None

    def check(self, longitude: float, latitude: float, school_position) -> list:
        """校验定位是否在签到范围，返回命中的校区/网格列表（取[0]用于打卡参数）。"""
        payload = {
            "mapType": "amap",
            "longitude": longitude,
            "latitude": latitude,
            "range": json.dumps(school_position, separators=(",", ":")),
        }
        data = self._enc_get("check.action", payload)
        return data if isinstance(data, list) else []

    def clock_in(self, payload: dict) -> dict:
        """提交打卡（GET + 加密 param），返回 {attn: {...}}。"""
        return self._enc_get("clockIn.action", payload) or {}


# ============ 面向上层的三个入口 ============


def query_today_task(client: AttnClient, cfg: dict) -> dict:
    """查询当天状态。

    返回 {"need_checkin": bool, "reason": str, "init": 响应}
    reason 取值：
      already   已签到 / 处理中
      need_not  当天无需签到（ClockWay=3）
      no_plan   服务器没下发考勤计划（无晚点名清单）
      todo      待签到
    """
    data = client.init()
    user = data.get("userData") or {}
    status = ((data.get("statusData") or {}).get("status") or "").upper()
    way = user.get("FWay")
    plan_id = str(user.get("FPlanID") or "").strip()

    if status in DONE_STATUSES:
        return {"need_checkin": False, "reason": "already", "init": data}
    if way == 3:
        return {"need_checkin": False, "reason": "need_not", "init": data}
    if not plan_id:
        return {"need_checkin": False, "reason": "no_plan", "init": data}
    return {"need_checkin": True, "reason": "todo", "init": data}


def collect_polygons(init_data: dict) -> list:
    """把 schoolData[].FPosition 里的所有多边形拼成一个 range 列表。"""
    polygons = []
    for sd in init_data.get("schoolData") or []:
        pos = sd.get("FPosition")
        try:
            obj = json.loads(pos) if isinstance(pos, str) else (pos or {})
            polygons.extend(obj.get("range") or [])
        except Exception:
            continue
    return polygons


def campus_list(init_data: dict) -> list:
    """教室/校区列表（新版字段是 ID + FName）。"""
    out = []
    for sd in init_data.get("schoolData") or []:
        out.append({
            "id": str(sd.get("ID") or sd.get("id") or ""),
            "name": sd.get("FName") or sd.get("name") or "",
        })
    return out


def do_checkin(client: AttnClient, cfg: dict, init_data: dict, dry_run: bool = False) -> dict:
    """按 init 数据 + 配置定位完成 check → clockIn 全链路。

    dry_run=True 时只走到「算好打卡参数」为止，不真正提交。
    返回 {"ok": True, "campus": 命中校区, "late": bool, "attn"/"payload": ...}
    定位不在范围内返回 {"ok": False, ...}。
    """
    user = init_data.get("userData") or {}
    params = init_data.get("paramsData") or {}
    longitude = float(cfg["checkin"]["longitude"])
    latitude = float(cfg["checkin"]["latitude"])

    # 1. 组装签到范围并做定位校验（schoolPosition = {range: [多边形...]}）
    polygons = collect_polygons(init_data)
    if not polygons:
        return {"ok": False, "reason": "no_range", "campus": None, "late": False}
    matched = client.check(longitude, latitude, {"range": polygons})
    if not matched:
        return {"ok": False, "reason": "out_range", "campus": None, "late": False}

    first = matched[0] if isinstance(matched[0], dict) else {}

    # 2. 迟到判定：以服务器时间为准（换算成北京时间），与 FLateTime（迟到线）比较
    ts = client.server_time()
    now = beijing_now(ts)
    late_line = str(params.get("FLateTime") or params.get("FEndTime") or "23:59").strip()
    try:
        h, m = map(int, late_line.split(":")[:2])
        is_late = now.hour * 60 + now.minute > h * 60 + m
    except Exception:
        is_late = False

    # 3. 实际打卡点：命中校区名 + 配置的地址文案（与前端 actualLocation 拼接方式一致）
    campus_title = first.get("title") or first.get("name") or ""
    addr = str(cfg["checkin"].get("actual_location") or "").strip()
    if campus_title and addr and campus_title in addr:
        actual_location = addr          # 文案里已经带了校区名，不重复拼
    else:
        actual_location = " ".join(x for x in (campus_title, addr) if x)

    # 4. 组装打卡参数（与新版前端 clockIn 的 payload 完全一致）
    payload = {
        "campus": first.get("id") or user.get("FDefaultCampus") or "",
        "lon": longitude,
        "lat": latitude,
        "startTime": str(params.get("FStartTime") or "").strip(),
        "endTime": str(params.get("FEndTime") or "").strip(),
        "actualLocation": actual_location,
        "caIsNo": "0",
        "collUnit": user.get("FCollUnit"),
        "way": user.get("FWay"),
        "isLate": 1 if is_late else 0,
    }
    if dry_run:
        return {
            "ok": True,
            "reason": "dry_run",
            "campus": campus_title,
            "late": is_late,
            "payload": payload,
        }

    attn = client.clock_in(payload)
    return {
        "ok": True,
        "reason": "done",
        "campus": campus_title,
        "late": is_late,
        "attn": (attn or {}).get("attn") or attn,
    }
