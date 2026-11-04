# fzu-auto-checkin · 福州大学晚点名自动签到

![License](https://img.shields.io/github/license/cccccheyu/fzu-auto-checkin) ![Python](https://img.shields.io/badge/python-3.11+-blue) ![GitHub Actions](https://img.shields.io/badge/cron-21%3A35%20%2F%2021%3A50-green)

<div align="center"><img src="assets/fzu-checkin-poster.png" width="420" alt="晚点名自动签到宣传图"></div>

福州大学「智汇福大」晚点名（21:30–23:59）的自动签到工具：**每晚定时自动打卡，结果微信推送**。手机零安装，iOS / 安卓都能用。

> ⚠️ **使用须知**
>
> 晚点名是学校的安全确认制度。本项目仅用于**本人确实在校时防止漏签**，禁止用于向学校隐瞒真实在/离校状态，禁止付费代挂。违规后果由使用者自行承担。

## 功能

- ⏰ 定时自动签到（默认每晚 21:35 / 21:50 / 22:05 / 23:00 / 23:05 / 23:30 / 23:55 七次，层层兜底）
- 📲 签到结果微信推送（Server酱 / PushPlus / Bark / 企业微信）
- 🔑 支持学号密码全自动登录，token 过期自动换新，无需手工维护
- 📍 定位校验预检：坐标不在校区内会明确报错，不会乱点

## 快速开始（3 步）

### 第 1 步 · 拿到代码

```bash
git clone https://github.com/cccccheyu/fzu-auto-checkin.git
cd fzu-auto-checkin
pip install -r requirements.txt
```

### 第 2 步 · 填配置

> 🖱️ **不想手填配置？用[网页版配置生成器](https://ab29158412654ce793c6cea3134abfb9.app.workbuddy.link)**：填完账号 / 位置 / 推送三步后，按「有没有 GitHub 账号」二选一 —— **有账号就贴个 Token 一键部署**（自动替你建私有仓库、写入代码和你的配置），没账号跟着分步引导走。另有**补假期工具**（只选起止日期即可，还能一键写回你的仓库）和**本机记忆**（填过的内容下次打开自动带出）。纯前端页面，不收集任何数据，也不记密码 / token。也可以把仓库里的 `web/configurator.html` 下载后双击离线使用。下面的手动流程照旧可用。
>
> 📣 **想拉同学一起用？发[宣传卡](https://04a8d547cb6c4c8c91a3e31b07ea326c.app.workbuddy.link)**：一页讲清痛点 + 卖点 + 二维码，扫码直达配置页，也可以直接转发链接。仓库里的 `web/promo.html` 就是同一张卡，可离线打开。

复制模板为 `config.yaml`（此文件已被 .gitignore 忽略，不会被提交）：

```bash
cp config.example.yaml config.yaml
```

逐项填写（每项怎么填都写在模板注释里）：

| 配置项 | 填什么 | 怎么获取 |
|--------|--------|----------|
| `user.username` / `user.password` | **登录智汇福大 App 的学号和密码**（推荐，全自动免维护） | 你平时登 App 用的那套 |
| `user.token` | 免登 token（可选项，与账密二选一即可） | 见 [docs/protocol.md](docs/protocol.md) §4 |
| `checkin.longitude` / `latitude` | 你在校区内宿舍楼的坐标 | 打开[高德坐标拾取器](https://lbs.amap.com/tools/picker) → 搜索你的宿舍楼 → 复制"经度,纬度" |
| `checkin.actual_location` | 打卡上报的地址文案 | 如「福州大学旗山校区生活区X号楼」 |
| `notify.*` | 推送渠道（填一种即可） | [Server酱](https://sct.ftqq.com) / [PushPlus](https://www.pushplus.plus) 注册即得 key |

### 第 3 步 · 跑起来

**先本地自检一次**（只读，不打卡，几秒出结果）：

```bash
python main.py --doctor
```

看到「一切正常，今晚 21:35 后会自动签到」就是配置正确。想先看会提交什么参数、且不真打卡：

```bash
python main.py --force --dry-run
```

**然后挂到 GitHub Actions 云端自动跑**（推荐）：

1. 在 GitHub 上 Fork 本仓库，或新建一个 **私有仓库** 推送这份代码
2. 仓库 `Settings → Secrets and variables → Actions → New repository secret`，名称填 `CONFIG_YAML`，值 = 你本地的 `config.yaml` 全文
3. 完成。每天北京时间 21:35 / 21:50 / 22:05 / 23:00 / 23:05 / 23:30 / 23:55 自动执行，结果推送到微信

> 💡 为什么推荐私有仓库：你的凭据只通过 Secret 注入，不进代码库；Actions 免费额度对私有库也够用（每天跑一次绰绰有余）。

## 常见问题

<details>
<summary><b>token 是什么？会过期吗？</b></summary>

登录态凭证。**2026-11 起学校对校外 IP 的学号密码登录加了短信二次验证**，脚本无法无人值守完成，所以现在推荐的做法是：

1. 在模拟器/手机上登录智汇福大 App，打开一次「业务 → 晚点名签到」页面；
2. 在电脑上运行：

   ```bash
   python tools/token_tool.py grab
   ```

   它会用 adb 读出 App 缓存里的 token，逐个用接口验证，把**当前有效**的那个写回 `config.yaml`。

token 什么时候失效不确定（几个月级别），失效后重复上面两步即可。`python main.py --doctor` 会直接告诉你 token 还有没有效。
</details>

<details>
<summary><b>迟到线是几点？</b></summary>

以服务器下发的 `FLateTime` 为准（2026-11-01 实测为 **22:30**）。21:30–22:30 打卡为正常，22:30 之后记迟到。定时主档设在 21:35/21:50/22:05/23:00/23:05/23:30/23:55；另有 20:05/20:35/21:05 提前档对冲 GitHub 定时延迟（准点到达窗口外会被静默跳过）。
</details>

<details>
<summary><b>报「登录凭证已失效」/「服务器暂未返回今日计划」？</b></summary>

这两个是不同的问题，v1.1 已经能区分：

- **登录凭证已失效**：token 过期或被服务端作废，接口返回 500 + 「请先登录！」。按上面的方法抓一个新 token 即可。
- **今日无考勤计划**：token 正常，但服务器没下发晚点名计划（`FPlanID` 为空），当天确实不用签。

跨午夜时段（约 0:00–1:00）服务器尚未发布当日计划，也可能短暂取不到数据，稍后再试即可。
</details>

<details>
<summary><b>为什么不能再用学号密码全自动登录了？</b></summary>

学校统一身份认证（sso.fzu.edu.cn）现在会对**非校内 IP** 强制短信二次验证，页面返回 `sso-second=true`。GitHub Actions 和家里的网络都属于校外 IP，所以密码登录必被拦住。除非在校园网内运行，否则只能用 token。
</details>

<details>
<summary><b>寒暑假怎么办？学期中间有不用签到的时段呢？</b></summary>

配置里的 `vacation.skip_ranges` 是**通用的**，任何人都可以按需插入自己的免签时间段（模板已预填官方校历的寒假 1.23–2.21、暑假 7.3–8.29，出处《福州大学 2026—2027 学年校历》）。学期中间加一条即可，比如离校参加比赛一周：

```yaml
vacation:
  notify: false
  skip_ranges:
    # ...已有的区间不动，往下追加：
    - name: 比赛离校
      start: "2026-11-02"
      end: "2026-11-08"
```

区间内脚本**不签、不推、不留记录**，Actions 照常跑但等于空转；区间结束自动恢复签到。新学年校历发布后更新已有区间的日期即可。注意：用 CONFIG_YAML secret 的用户直接改 secret 内容，改完无需提交任何代码。
</details>

<details>
<summary><b>人不在学校会怎样？</b></summary>

脚本不做真实定位——这是特性也是红线：**请在本人确实在校时使用**。长期离校（实习、交换等）请走辅导员报备请假，名正言顺。
</details>

<details>
<summary><b>iOS 用户能用吗？</b></summary>

能，而且是最省事的用法：脚本跑在 GitHub Actions 云端，iPhone 只收微信推送，什么都不用装。
</details>

## 工作原理

晚点名页是一个 H5 页面，认证只靠一个免登 token。本项目通过静态分析其前端代码逆向出完整协议：4 个接口（查状态 / 校时 / 定位校验 / 打卡）+ AES-CBC 参数加密，无需 Frida、无需抓包工具。完整逆向笔记见 [docs/protocol.md](docs/protocol.md)。

另附 v0.1 Android 无障碍脚本（`scripts/autojs_wandianming.js`）：模拟点击方案，不碰接口，供参考。

## 参与贡献与反馈

- **用着有问题、发现 bug、想要新功能**：欢迎发邮件到 ``
- 适配其他学校或系统：欢迎提 Issue / PR。提交内容请**脱敏**（密码、学号、token 一律打码）。

本项目不接受、不鼓励任何形式的代挂与有偿使用。

## License

MIT
