# CampusNet · 校园网助手

**一个专注「把校园网连上、把网速看清楚」的 Windows 桌面客户端。**

自动连接校园 WiFi、自动完成 Portal 认证、掉线自动重连，顺带把流量统计、网络诊断、账户与套餐查询都放进同一个 FluentUI 界面。

![python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![platform](https://img.shields.io/badge/Windows-10%20%2F%2011-0078D4?logo=windows&logoColor=white)
![gui](https://img.shields.io/badge/GUI-PyQt5%20%2B%20FluentUI-41CD52?logo=qt&logoColor=white)
![license](https://img.shields.io/badge/License-MIT-yellow.svg)

---

## ✨ 功能

| 模块 | 能力 |
| --- | --- |
| **自动连接** | 监听 SSID，命中白名单后自动连接 WiFi → 探测 Portal → 提交认证，全程无需点击 |
| **自动重试** | 认证失败按间隔重试；检测到掉线自动重连；可配置「在线数已满」时抢占最早设备 |
| **状态面板** | 连接状态、在线时长、本月上下行流量、套餐名称与到期时间，全部写在控件里，没有额外的小字说明 |
| **流量统计** | 读取网卡计数器，按小时/天聚合；趋势默认近 7 天，可切「上一个 7 天」或自定义日期；默认只记录校园网环境，热点/家宽不计入；支持曲线图、按月翻页的日历年热力图（每格显示日号，可点某天看用量）、CSV 导出、保留天数清理 |
| **一键网络诊断** | 连通性、延迟/抖动、DNS 解析、下载与上传测速合并为一个板块：点一次「开始测试」按顺序跑完五项，进度、结果表、失败原因都在控件里 |
| **无线网络** | 打开即显示已保存的配置和已知校园网（不是空白列表），后台扫描补齐信号强度与信道，手动连接/断开 |
| **账户与设备** | 登录自助系统查看在线设备、套餐余量、订单记录，支持远程下线指定设备 |
| **上网明细** | 按天查看学校侧的上网记录（开始/结束、时长、上下行、IP、设备 MAC）；日期可用日历选择，也可以「上一天 / 下一天」按当前日期前后翻，进入页面自动查当天 |
| **暂停 / 恢复** | 放在「账户状态」折叠卡里，默认收起；申请暂停需要手动输入确认语句。学校侧该功能可能已停用，面板里直接给出校园网服务中心的线上入口 |
| **校园网公告** | 主页顶部的公告卡展示学校发布的公告与页脚文案；右上角 × 关掉后不再打扰，公告内容变了才会重新出现，「设置 → 界面与启动 → 主页显示校园网公告」可重新打开 |
| **单实例运行** | 同时只会有一个进程在跑：再次双击图标会把已打开的窗口叫到前台，不会重复采样、重复写历史 |
| **托盘常驻** | 托盘图标显示状态，右键快速连接/断开，关闭窗口可选最小化到托盘 |
| **开机自启** | 写入计划任务（而不是 Run 键），确保要求管理员权限的程序也能正常启动 |
| **内置浏览器** | 紧凑工具条（地址栏与按钮等高）、地址栏状态图标、加载失败页内嵌「重新加载 / 用系统浏览器打开」、容忍自签证书、`Ctrl+L` / `F5` / `Alt+←→` / `Esc` 快捷键、记住窗口大小，新窗口在当前视图打开；重定向、页面内跳转、自定义协议子框架导致的加载失败都不会被误判成「网页打不开」（失败时会用 HTTP 复核一次，有内容就保留页面） |
| **设置** | Fluent 风格两级导航：先看到分组条目列表（通用 / 连接 / 流量与用量 / 通知与启动 / 关于，圆角条目 + 图标 + 副标题 + 右箭头），点一行进入该组；二级页顶部是 Win11 式页头（返回箭头与 28px 标题同一行 + 14px 说明），组内按卡片分区 |
| **关于** | 设置列表最后一组里的品牌卡：版本信息、项目主页 / 问题反馈 / MIT 许可证、开源组件致谢 |
| **自定义名称** | 设置里可以给程序改个名字，窗口标题、托盘提示与「关于」卡同步显示 |
| **主题** | 浅色 / 深色 / 跟随系统，Windows 11 下支持云母（Mica）材质，切换即时生效 |
| **开发者模式** | 默认关闭；打开后出现「开发者」页，分「接口浏览 / 站点地图 / 运行环境 / 运行日志」四个标签：可逐个调用接口并查看原始返回，还能一键抓取整站地图——把学校服务器上可匿名访问的页面、静态资源与接口分类列出（页面/接口/资源/脚本里拼出的隐藏地址/失败），双击打开或复制 |

所有耗时操作都在线程池里跑，界面不会卡住；认证、掉线、冲突等关键事件通过系统通知提醒，可一键静音。

## 🧱 架构

```mermaid
flowchart LR
    subgraph GUI["src/gui"]
        A[MainWindow<br/>FluentWindow] --> B[主页]
        A --> C[网络工具]
        A --> D[设置]
        A --> E[开发者 · 可选]
    end
    subgraph CORE["src/core"]
        F[Service<br/>状态机 + 调度] --> G[TrafficMonitor]
        F --> H[Settings<br/>DPAPI 凭据]
    end
    subgraph NET["src/net"]
        I[wifi] --> J[probe] --> K[http]
        L[diagnostics]
    end
    subgraph PORTAL["src/portal"]
        M[detect] --> N[auth] --> O[lanhai / generic / selfbase]
    end
    GUI --> F
    F --> I
    F --> N
    A -.内置浏览器.-> P[QtWebEngine]
```

- `src/core/service.py` 是唯一的状态机：检查 → 连 WiFi → 认证 → 在线 → 掉线重试。
- `src/portal/*` 按「识别门户 → 选驱动 → 提交认证」拆分，新增学校只需加一个驱动。
- `src/net/http.py` 统一封装请求（优先 `requests`，缺失时回退 `urllib`），忽略自签证书、保留重定向信息。
- `src/net/sitemap.py` 是开发者页「校园网站点地图」用的同源爬虫，只抓可匿名访问的地址，不登录、不提交表单。

## 🚀 快速开始

### 环境要求

- Windows 10 / 11（x64）
- Python 3.10+（推荐 3.12 / 3.14）
- 需要管理员权限：连接 WiFi、读取网卡计数器和写计划任务都需要它。源码运行时程序会自动请求提权；打包出的 exe 内置 `requireAdministrator` 清单，双击即弹 UAC

### 从源码运行

```powershell
git clone https://github.com/<your-name>/CampusNet.git
cd CampusNet

py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt

py main.py
```

> 仓库与安装包里**不含任何账号密码**。首次启动时账号、密码输入框都是空的，请填自己的校园网账号；凭据只以 DPAPI 密文保存在你本机的 `%APPDATA%\CampusNet`。

自检（不启动界面，逐项检查依赖与能力）：

```powershell
py main.py --selftest
```

### 打包成单文件 exe

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
# 产物：dist\CampusNet.exe
```

`build.ps1` 已内置 QtWebEngine、pyqtgraph、qfluentwidgets 等隐式依赖和资源目录；打包后 `dist\CampusNet.exe` 可直接分发，目标机器无需安装 Python。

## 🧭 使用指南

1. **首次启动** 打开「设置 → 账号」，填入校园网账号密码并保存（密码用 Windows DPAPI 加密，只在本机可解密）。
2. **主页** 显示当前状态；点「一键连接」可立即走一遍完整流程。
3. **网络工具** 分六个分区：无线网络、网络诊断、流量统计、账户与设备、自助服务、运行日志。
4. **流量趋势** 默认最近 7 天；下拉可切「上一个 7 天」或「自定义…」选起止日期，旁边的「上一天 / 下一天」把整段窗口前后平移一天，最新一段时「下一天」置灰。
5. **上网明细** 在「账户与设备」里，日期选好后点「查询」，或直接用「上一天 / 下一天」按当前日期前后翻；选中今天时「下一天」置灰。
6. **暂停 / 恢复账号** 在页面最底部「账户状态」卡里，点开才出现按钮；暂停时会要求输入确认语句，操作前请先确认学校侧是否仍支持。
7. **托盘** 双击图标唤起窗口；右键菜单可快速连接 / 断开 / 退出。程序是单实例的，再次双击图标只会把已打开的窗口叫出来。
8. **开发者模式** 默认关闭，在「设置 → 界面与启动」里打开后，导航底部会出现「开发者」页；切到「站点地图」标签，输入入口地址后点「开始抓取」，即可列出整站页面、接口、静态资源，以及脚本里拼出来的隐藏地址（快照见 [docs/SERVER_MAP.md](docs/SERVER_MAP.md)）。

> 提示：程序只负责「连接 + 认证 + 观测」，不会修改系统网络配置，也不会代理任何流量。

## ⚙️ 配置项

配置保存在 `%APPDATA%\CampusNet\settings.json`，也可以在设置页里改。

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `auto_connect` | `true` | 检测到白名单 SSID 时自动连接 |
| `auto_login` | `true` | 连上 WiFi 后自动提交认证 |
| `wifi_allow_list` | `NYKJ` 等 4 项 | 允许自动连接的 SSID；忽略大小写与 `-` `_` 分隔符，按包含匹配 |
| `driver` | `auto` | 认证驱动：`auto` / `lanhai` / `generic` |
| `retry_count` | `3` | 认证失败重试次数 |
| `retry_delay_ms` | `8000` | 重试间隔 |
| `auto_retry_on_drop` | `true` | 掉线后自动重试 |
| `auto_reconnect_on_logout` | `true` | 被强制下线后自动重连 |
| `kick_on_conflict` | `false` | 在线数已满时下线最早设备再登录 |
| `notify_on_drop` | `true` | 掉线时通知 |
| `show_notification` | `true` | 系统通知**总开关**（关掉后所有通知都不弹） |
| `traffic_interval_ms` | `5000` | 流量采样间隔 |
| `traffic_retention_days` | `120` | 历史记录保留天数 |
| `traffic_only_campus` | `true` | 只记录校园网环境的流量；连接热点/家宽时不写入历史与用量 |
| `quota_auto` | `true` | 从自助系统自动读取本月额度 |
| `monthly_quota_gb` | `0` | 手动额度；`0` 表示不统计配额 |
| `app_name` | `""` | 自定义程序名（窗口标题、托盘、关于页）；留空用 `CampusNet` |
| `theme` | `auto` | `auto` / `light` / `dark` |
| `mica_effect` | `true` | 云母材质（Win10 无效，切换即时生效） |
| `close_to_tray` | `true` | 关闭窗口时最小化到托盘 |
| `auto_launch` | `false` | 开机自启（写计划任务） |
| `start_minimized` | `false` | 启动后直接缩到托盘 |
| `developer_mode` | `false` | 显示「开发者」页 |

## 📁 目录结构

```
CampusNet/
├── main.py                  # 入口：--selftest / --connect-only / 正常启动
├── build.ps1                # PyInstaller 打包脚本
├── requirements.txt
├── resources/               # 图标、托盘图、WiFi 与流量统计 PowerShell 脚本
└── src/
    ├── core/                # service（状态机）、settings（DPAPI）、traffic、winutil…
    ├── net/                 # wifi、probe、http、diagnostics
    ├── portal/              # detect、auth、lanhai、generic、selfbase、selfservice
    └── gui/                 # app、pages、charts、widgets、theme、browser…
```

## ❓ 常见问题

**为什么 exe 有 130 MB？**
QtWebEngine（Chromium）本身就占大头；单文件模式还会把运行库一起塞进去。想瘦身可以改用 `--onedir` 或去掉内置浏览器。

**启动时报 `Could not create temporary directory!`**
单文件模式依赖 `%TEMP%` 解压。若你在 MSYS2 / Git Bash 里运行，`TEMP` 可能被设成 `/tmp` 这类 POSIX 路径，改回 `C:\Users\<你>\AppData\Local\Temp` 即可。

**点了关闭，程序还在运行？**
默认「关闭时最小化到托盘」，可从托盘右键退出，或在设置里关掉这一项。

**测速一直转圈？**
测速源可能被校园网关拦截。当前版本对每次测速都有总时长上限，超时会自行结束并提示；也可以在诊断页只测下载。

**通知关不掉？**
「系统通知」是总开关，关掉后认证、掉线、冲突提示都不会弹；`notify_on_drop` 只是掉线通知的细分开关。

**需要管理员权限吗？**
需要。连接/断开 WiFi、读取网卡计数器、写计划任务都要管理员权限：源码运行时程序自动请求提权，打包出的 exe 带 `requireAdministrator` 清单，双击就会弹 UAC。

**程序里会不会带着别人的账号？**
不会。仓库与发行包里没有任何账号密码，所有凭据都是使用者在本机首次填写、用 DPAPI 加密后存在自己的 `%APPDATA%` 下。

**学校门户不一样怎么办？**
设置里可填「自定义 Portal 地址」，并把驱动切成「通用 Portal（表单提交）」；若仍不适配，`src/portal/` 下新增一个驱动即可。

## 🔐 数据与隐私

- 仓库、源码与发行包中**不存在任何账号密码**；账号由使用者首次运行时自行填写。
- 账号密码用 Windows **DPAPI**（`CryptProtectData`）加密，密文与当前 Windows 用户绑定，拷走也无法解密。
- 所有运行数据（配置、流量历史、日志、内置浏览器缓存）都在 `%APPDATA%\CampusNet`，不会上传到任何第三方。
- 程序只访问校园网 Portal 与自助系统；测速会连接公共测速源（可关闭）。

## 🛠 开发

```powershell
py -m compileall -q src main.py   # 语法检查
py main.py --selftest             # 能力自检
py -m pip install pyinstaller     # 打包依赖
```

代码风格：PEP 8、四空格缩进、类型注解齐全、模块级 `from __future__ import annotations`；界面层不写阻塞调用，统一走 `run_async`。

## 🙏 开源与致谢

本项目基于这些优秀的开源项目构建：

| 项目 | 用途 | 许可 |
| --- | --- | --- |
| [PyQt5](https://riverbankcomputing.com/software/pyqt/) / [Qt](https://www.qt.io/) | 窗口、控件与内置浏览器（QtWebEngine） | GPL v3 / 商业 |
| [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) | Fluent Design 组件库 | GPL v3 |
| [PyQt-Frameless-Window](https://github.com/zhiyiYo/PyQt-Frameless-Window) | 无边框窗口与云母材质 | GPL v3 |
| [pyqtgraph](https://www.pyqtgraph.org/) | 流量曲线与柱状图 | MIT |
| [requests](https://requests.readthedocs.io/) / [urllib3](https://urllib3.readthedocs.io/) | HTTP 请求（缺失时自动回退标准库） | Apache-2.0 / MIT |
| [pywin32](https://github.com/mhammond/pywin32) | DPAPI 加密、窗口与进程操作 | PSF |

设置页底部也有同样的「关于」板块，可直接跳转本项目与上述组件的主页。

## 📄 许可证

[MIT](LICENSE)

## ⚠️ 免责声明

本项目仅供学习与个人使用，与任何学校、运营商或设备厂商无关。请遵守所在学校的网络使用规定；使用本工具产生的任何后果由使用者自行承担。
