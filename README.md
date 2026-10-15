# fanka-monitor 🍚

完美学堂 / 新中新（17wanxiao）校园一卡通**余额监控 + 消费明细推送**工具。

给娃的饭卡总在不知不觉中见底？这个脚本每天定时推送余额和最近消费明细，
余额低于阈值立即告警 —— 再也不怕孩子饿肚子。

```
🍚 饭卡余额：28.90 元
⚠️ 已低于 30 元，记得充值！

📋 最近 3 天消费：
10-15 12:42  小超市01  -3.00
10-15 12:26  消费机1楼3号窗  -6.00
10-14 12:47  小卖部2  -2.00
10-14 12:28  2楼17号窗口  -8.50
```

## ✨ 特性

- **账号密码直登**：每次运行自动登录换取全新会话，不维护任何 token —— 永不失效
  （手机上正常使用小程序把旧会话踢下线也不影响）
- **余额日报**：余额 + 最近 3 天消费明细（哪个窗口、花了多少）
- **低余额告警**：低于阈值才输出，**同类告警每天最多一条**（充值后需实体卡
  圈存才能入账，重复告警没有意义）；余额恢复后自动重置
- **明细归档**：全部历史交易按时间戳去重，累积保存在本地
- **零依赖**：纯 Python 3.9+ 标准库，不需要 pip install 任何东西
- **推送通道无关**：结果输出到 stdout，配 cron + 任意推送（Server酱、
  Telegram Bot、企业微信/飞书/钉钉 webhook、ntfy……）皆可

## 🔍 原理

支付宝「完美学堂」小程序（K12 版）的一卡通后端是**新中新 17wanxiao ecard H5**。
H5 页面有一套网页版登录链路，本项目将其自动化：

```
GET  open.17wanxiao.com/api/authorize        → 建立登录 SESSION
POST open.17wanxiao.com/api/login            → 账号密码登录（form: loginType=ecard…）
GET  open.17wanxiao.com/api/authorize        → 302 跳转，带出授权 code
GET  ecardh5.17wanxiao.com/ecardh5/bootcallback?…&code=…
                                             → 签发 ECARD_H5_SESSION 等会话 cookie
POST ecardh5.17wanxiao.com/ecardh5/bootcallback
     gotowhere=XYK_BASE_INFO                 → 余额
     gotowhere=XYK_TRADE_DETAIL              → 消费明细
```

> 注意：`/api/login` 的 query string 必须带 `client_id`、`redirect_uri` 等
> 全套参数（与 authorize 相同），否则返回 `bad client_id`。

## 🚀 快速开始

### 1. 获取配置参数（约 10 分钟）

需要抓一次包，拿到你学校/账号的 7 个参数。两种方式任选：

**方式 A：电脑浏览器开发者工具（推荐，最简单）**

1. 用电脑浏览器打开一卡通 H5 页面（可从手机小程序进入后复制链接，
   或用方式 B 先抓一次入口 URL）
2. `F12` → Network → 勾选 Preserve log
3. 操作到登录页面，筛选 `authorize`：
   - 请求 URL 里取 `customerCode`、`client_id`
   - URL 里 `redirect_uri` 参数（URL 编码的内层链接）解码后取 `openid`（= `aliUid`）、`flag`
4. 输入姓名、密码登录，筛选 `login`：表单里取 `outid`（`username`/`password` 就是你自己输入的）

**方式 B：iPhone + mitmproxy（抓小程序流量）**

<details>
<summary>展开步骤</summary>

1. Mac 上 `pip install mitmproxy`，然后 `mitmdump -w capture.flows --listen-port 8890`
2. `ipconfig getifaddr en0` 查看 Mac 局域网 IP
3. iPhone：设置 → 无线局域网 → 当前 WiFi ⓘ → 配置代理 → 手动 → 服务器填 Mac IP，端口 8890
4. 安装 CA 证书：把 `~/.mitmproxy/mitmproxy-ca-cert.cer` AirDrop 到手机
   （浏览器访问 mitm.it 的方式不可靠，Safari 会绕过代理）→ 设置中安装描述文件 →
   设置 → 通用 → 关于本机 → **证书信任设置** → 启用 mitmproxy
5. **强制退出支付宝**（上滑杀掉后台，否则小程序从缓存恢复不发请求）→
   重新打开 → 完美学堂 → 进入余额页面
6. 分析抓包：`python3 capture_analyze.py capture.flows`
7. 完事后记得：手机关代理、关证书信任；Mac 上 Ctrl+C 停掉 mitmdump

</details>

### 2. 填写配置

```bash
mkdir -p data
cp config.example.json data/config.json
chmod 600 data/config.json   # 含密码，务必收紧权限
# 编辑 data/config.json，填入 7 个参数
```

### 3. 测试

```bash
python3 fanka.py balance
```

看到余额和消费明细即成功。

### 4. 定时 + 推送

脚本把结果输出到 stdout，配合 cron 和任意推送通道即可。示例：

```cron
# 每天 8:00 日报（余额 + 最近 3 天明细）
0 8 * * * /path/to/push.sh "$(/usr/bin/python3 /path/to/fanka.py balance)"

# 每 2 小时静默检查（余额正常时无输出，低于阈值或出错才输出）
17 */2 * * * msg=$(/usr/bin/python3 /path/to/fanka.py check --threshold 30) && [ -n "$msg" ] && /path/to/push.sh "$msg"
```

推送通道示例（Server酱）：

```bash
msg=$(python3 fanka.py check --threshold 30)
[ -n "$msg" ] && curl -s "https://sctapi.ftqq.com/YOUR_SENDKEY.send" \
  --data-urlencode "title=饭卡提醒" --data-urlencode "desp=$msg"
```

## 📖 命令说明

| 命令 | 行为 | 适用 |
|---|---|---|
| `balance` | 输出余额（低于阈值附带提醒）+ 最近 3 天消费明细，并归档明细 | 每日日报 |
| `check` | 余额正常时**无输出**；低于阈值输出告警；出错输出错误 | 高频检查 |
| `--threshold N` | 余额阈值，默认 30 | 两个命令通用 |

- 退出码：成功 0，失败 1
- **告警去重**：`check` 的「低余额」和「查询失败」告警各自每天最多一条
  （记录在 `data/alert_state.json`）；余额恢复到阈值之上后自动重置
- 明细失败不影响余额输出（降级为附带一行提示）

## ⚙️ 配置项（data/config.json）

| 字段 | 说明 | 来源 |
|---|---|---|
| `username` | 学生姓名（一卡通绑定姓名） | 登录时输入的 |
| `password` | 一卡通查询密码 | 登录时输入的 |
| `customerCode` | 学校客户代码 | authorize 请求 URL 的 `customerCode` |
| `outid` | 学生学工号 | `/api/login` 表单的 `outid` |
| `openid` | 支付宝用户 ID（2088 开头） | redirect_uri 解码后的 `openid`/`aliUid` |
| `client_id` | ecard H5 应用 ID（32 位 hex） | authorize 请求 URL 的 `client_id` |
| `flag` | 授权 flag | redirect_uri 解码后的 `flag` |

## 📁 目录结构

```
fanka.py              # 主程序（余额/明细查询、告警去重、归档）
config.example.json   # 配置模板
capture_analyze.py    # mitmproxy 抓包分析工具（仅部署期使用）
data/                 # 运行时数据（gitignore，勿提交）
  config.json         # 你的配置（含密码，chmod 600）
  cookies.json        # 最近一次的会话 cookie（调试用）
  transactions.json   # 消费明细累积归档
  alert_state.json    # 告警去重状态
```

## 🔒 安全与隐私

- `data/` 整个目录在 `.gitignore` 中，**任何情况下不要提交**——里面有你的密码
- 建议 `chmod 600 data/config.json`
- 本工具仅以你自己的账号查询自己的卡，请自行承担使用责任；
  请求频率请保持克制（建议日报 1 次 + 检查每 2 小时 1 次）

## ❓ FAQ

**Q: 提示「密码登录失败」？**
卡密码可能在一卡通 App / 小程序里被修改过，同步更新 `data/config.json` 即可。

**Q: 充值后余额怎么没变？**
线上充值后需要孩子拿实体卡在食堂/圈存机上刷一次（圈存）才会入账，
这是这套一卡通系统的固有机制。

**Q: 会和其他设备抢登录吗？**
每次运行都会登录一次拿新会话，可能使 H5 页面旧会话失效；但支付宝小程序
下次打开会自动重新授权登录，用户无感知。

**Q: 想查更早的消费记录？**
`data/transactions.json` 会持续累积；接口本身支持 `beginIndex` 翻页和自定义
`beginDate`/`endDate`，改 `query_transactions()` 即可。

## License

MIT
