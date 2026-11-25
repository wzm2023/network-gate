# Network Gate 备用网络

这是对 VPN Gate SSTP + Cloudflare 方案的部署与修复版本，适合备用与实验。志愿者出口不保证固定 IP、独享带宽或连续在线。

## 组成

- `vpngate.py`：发现节点、通过有鉴权的 Worker 检测、执行质量筛选。
- `workers/checker.mjs`：限定 VPN Gate 目标，保留未知属性，支持 IP 查询故障回退。
- `workers/edge.mjs`：edgetunnel 入口、Cloudflare KV 节点快照、原生 Clash / v2ray 订阅。
- GitHub Actions：每小时第 17、47 分更新；发布失败保留上次快照；验证实际 Pages 结果。
- 独立客户端：仅监听 `127.0.0.1:17898`，不开启 TUN，不设置系统代理、默认路由或系统 DNS。

## 已修复

- 未知属性误标住宅；保留 VPN / proxy / abuse 标记，过滤明确滥用记录。
- 线程池退出导致整体截止时间失效；改为可取消异步 HTTP 请求。
- Windows 跨盘输出导致 `relpath` 报错。
- 无鉴权检测接口与任意目标探测；新增独立检查凭据及域名限制。
- 默认关闭自动更新、过期结果无提示、外部订阅转换依赖。
- 原生订阅包含 TLS 校验、失败切换和明确 SSTP 出口；过期快照返回 503。
- 节点名单优先从自身 KV 读取；GitHub Pages 用于状态查看与备用读取。
- SSTP 收包不处理 TCP 重传和乱序导致 TLS 数据损坏：新增有界重组、重叠去重、32 位序号回绕及 FIN 顺序处理。
- 本机独立客户端每 30 分钟刷新，失败保留现有配置，停止时仅关闭自己启动的子进程。

“ISP 估计”仍是根据数据库和运营商名称推断，不能作为住宅或信誉证明。实际下载速度与稳定性必须另测。

## GitHub 设置

Repository variables：`CHECKER_HOST`、`EDGE_HOST`。

Repository secrets：`CHECK_TOKEN`、`PUBLISH_TOKEN`。两项分别用于检查和发布，不需要存放 Cloudflare 全局 API Key。

Pages Source 设置为 GitHub Actions。执行 `VPN Gate Node Check` 可立即刷新。自动维护记录每 14 天最多提交一次，用于记录成功更新及维持仓库活动；GitHub 调度仍不承诺准点。

## 订阅接口

- `/subscription/clash?token=...`：JSON 格式的完整 Clash/Mihomo 配置（JSON 是 YAML 的子集）。
- `/subscription/v2ray?token=...`：Base64 编码 VLESS 链接列表。
- `/status?token=...`：节点数量、更新时间、过期时间。
- `/health`：不含凭据的服务存活状态。

实际私人订阅和后台密码保存在部署机器 `.private` 目录，禁止提交到仓库。

## 本机使用

部署机器双击 `启动备用网络.cmd`；在需要使用的应用里填 HTTP/SOCKS5 代理 `127.0.0.1:17898`。

`查看备用网络.cmd` 查看进程及刷新状态，`刷新备用节点.cmd` 请求立即重新下载订阅，`停止备用网络.cmd` 只停止本项目客户端。

系统原代理继续保持原状。独立客户端不设置开机启动；重启 Windows 后需要再次运行启动脚本。只有明确配置了 17898 的应用使用这条链路。

节点名称中的国家及 ISP 来自检查时的数据库结果，不同数据库或出口变化会产生差异，不能保证应用实际看到的地区。

部署记录、已测结果与剩余限制见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 检查

标准环境：`pip install -r requirements.txt -r requirements-dev.txt`，然后执行 `pytest`、`ruff check .` 和 `node --experimental-loader ./tests/cloudflare-loader.mjs --test tests/*.test.mjs`。

当前 Windows 环境使用项目 `.deps` 目录隔离依赖，执行 `python scripts/local_checks.py`。不修改全局 Python 安装。

## 上游

上游提交与内容摘要保存在 `workers/upstream.json`。`workers/vendor` 内的源码保留各自许可证；原始 gate 项目说明见 `docs/UPSTREAM_README.md`。本项目的额外配置和包装模块不改变上游版权归属。

更新上游前先在隔离环境验证，不自动追随远端 main。`prepare_workers.py` 会在预期补丁位置不匹配时停止。
