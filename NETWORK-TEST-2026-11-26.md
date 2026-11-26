# 安卓 Karing / TikTok 实际链路测试

测试日期：2026-11-26，北京时间约 15:38–15:53。

结论：这套免费住宅候选链路可以连通，但当前吞吐、连接建立耗时和失败率不足以证明它适合稳定刷视频。用户从 JP-07 切换节点后反馈手机已连通，但仍然很慢。

## 测试方法

从这台 Windows 机器建立独立 Mihomo 测试进程，仅监听 `127.0.0.1:17908`，管理端口 `19093`。每次请求新建连接，通过选定出口访问真实 HTTPS 网站；未更改原代理、系统代理、路由、DNS 或 TUN。测试进程结束后清理其自己的子进程。

请求包含 Cloudflare 出口信息、Google / YouTube 204、TikTok 首页及 Cloudflare 1 MiB / 5 MiB 下载。TikTok 首页最多读取约 128 KiB，只能证明网页链路，不等于完整 App 视频播放测试。吞吐按实际收到的数据 / 总耗时计算，包含建连开销。HTTP 成功率也不是 IP 丢包率。

## 结果

| 节点 / 快照 | 请求结果 | 下载结果 |
| --- | --- | --- |
| 15:11 快照 JP-07，`vpn569190458.opengw.net:1513` | 两轮共 10 次，成功 3 次；TikTok 两次均在 TLS 握手断开 | 1 MiB 两次，一次失败，一次 19.85 秒，0.423 Mbps |
| 同一快照 JP-02，`vpn481731763.opengw.net:1248` | 5 轮、约 128 秒，共 25 次成功 24 次；TikTok 首页 5 次均有 HTTP 200 响应 | 1 MiB 成功 4/5；成功样本 0.572–0.696 Mbps，平均 0.640 Mbps |
| 15:43 快照 US-01，`vpn445617380.opengw.net:1841` | 出口、TikTok 首页、1 MiB 下载初测均成功 | 1 MiB：0.881 Mbps；另测 5 MiB 两次，一次握手失败，一次 36.724 秒、1.142 Mbps |
| 15:43 快照其他 5 个日本候选 | 其中一条 3 个测试只通过 1 个；其余小样本可用 | 1 MiB 成功样本 0.220–0.653 Mbps |
| 原有 7897 代理对照 | 同样目标可访问 | 5 MiB：14.476 秒、2.897 Mbps |

JP-07 的检查服务复测也返回 `SSTP server connection timed out`；同次检查 JP-01 正常。失败不是仅由 TikTok 网站单独拒绝造成。JP-02 连通后，建连 / 收到 HTTP 头常需 4–7 秒，叠加多个 App 请求会明显拖慢体验。

这些数据只代表上述机器、出口和测试时段；手机网络路径可能不同，不能据此保证手机相同速率。所有详细记录只保存在 `.private`：`jp07-diagnosis.json`、`alternatives-diagnosis.json`、`jp02-stability.json`、`fresh-candidates-speed.json`、`sustained-download.json`。

## 本次修正

- 新增 `/subscription/karing`，仍需原订阅凭据，只包含住宅候选。
- 使用 Karing 支持的 `url-test` 自动选择组。此前通用 V2ray 链接只有节点，不能包含电脑 Python 守护程序的恢复逻辑；Karing 官方兼容表也不支持 Clash 的 `fallback` 策略。
- 新订阅用国家、实际主机名、端口标识节点；不再仅以随延迟排名变化的 JP-02 / JP-07 表示身份。
- 保留 TLS 证书验证、`udp: false`。服务端当前不支持普通 UDP/QUIC 转发；DNS 和 TUN 由 Karing App 自己管理，订阅无法代替手机内的 DNS 设置。
- 增加独立实测脚本 `scripts/diagnose_network.py`；可以固定快照复测具体节点，避免更新后测到同编号的另一台服务器。

官方依据：[Karing Clash 兼容列表](https://karing.app/clash)、[DNS 手册](https://karing.app/app-manual/dns)、[常见问题](https://karing.app/faq)。

后续格式修复：用户在 Android Karing 1.2.25.2802 导入时出现 `JSON UNKNOWN FORMAT`。原响应采用 JSON 写法的 Clash 配置；它通过 Mihomo 校验，仍未被此版本 Karing 的自动格式识别接受。现已在原地址输出以 `proxies:` 开头的 Clash YAML，并设置 `application/yaml`。回归检查覆盖实际 HTTP 响应格式、节点 / 分组 / 路径保持一致及鉴权，线上 YAML 解析通过；用户已确认该安卓版本成功导入。此前仅用 Mihomo 解析来代替 Karing 导入验证是不充分的。

## 手机使用

新的 Karing 专用地址保存在 `.private/使用信息.md`。导入后选择“住宅候选-自动选择”；它根据可达性与延迟选择节点，并不是带宽测速择优，实际测速间隔受 Karing 的设置和版本影响。也可手动测试当前列表中的美国 `vpn445617380` 候选，前提是它仍在线且通过住宅候选筛选。

当前手机已恢复连接，保留现有可用设置即可。若再次出现部分 App 无网，先用 Karing 的“连接状态 / 网络检测”区分失败域名、出站和错误；官方 FAQ 特别指出 Cloudflare 转发节点与其 DNS 配置的兼容问题。IPv6、Mux、DNS、UDP 路由的兼容问题需要手机侧日志确认，本次没有把它们断言为手机故障根因。

本次改动改善分组兼容性、身份追踪和问题诊断，不增加出口带宽，也不保证原有 TCP 会话在换节点后继续。单次低延迟或短请求成功不能作为视频可用性与长期稳定性证明。
