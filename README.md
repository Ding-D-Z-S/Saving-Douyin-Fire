# 🔥 抖音续火花控制台（Saving-Douyin-Fire）

一个 **仅本机使用、可视化、双击 BAT 启动** 的抖音续火花控制台。

- 🔒 仅监听 `127.0.0.1:6161`，只有本机能访问，**不暴露公网**
- 🚀 复用上游的 Python + Playwright 抖音发送核心
- 🖱️ 左侧仿抖音会话栏，右侧每日定时计划，所见即所得
- 💬 内置飞书应用机器人，可远程用 `0 / 1 / 2` 操控并接收任务通知
<img width="2492" height="1381" alt="image" src="https://github.com/user-attachments/assets/46f1894f-4624-48ff-a36e-5a1b77d0e4be" />
<img width="2472" height="1372" alt="image" src="https://github.com/user-attachments/assets/a1fa0486-d2b6-4c67-b1df-8949b1cf08fc" />

---

## ✨ 功能一览

| 模块 | 说明 |
| --- | --- |
| 扫码登录 | 首页点「扫码生成登录状态」，抖音 App 扫码即保存登录态 |
| 真实会话读取 | 只读抖音**左侧会话栏**：头像、名称、火花状态、火花天数、重燃/即将消失、摘要。**不读聊天正文** |
| 仿抖音列表 | 配置页左侧复刻抖音会话栏样式，头像、原生火花图标、火花天数 |
| 火花优先 | 有火花的会话排在前面，各分区内部保持抖音原顺序 |
| 勾选发送 | 默认**全部不勾选**；「自动勾选火花」是独立按钮 |
| 状态保留 | 刷新会话数据后保留勾选；实时显示「目前发送会话人有：xxx」 |
| 命名分组 | 从当前勾选一键创建分组、可自定义名称、可删除 |
| 每日计划 | 多条定时计划：分组 / 每日 / 时 / 分 / 发送内容 / **发送间隔** / 测试发送 / 删除 |
| 发送间隔 | 单人分组自动忽略；多人分组用该间隔（±20% 随机抖动，2–300 秒） |
| 真实测试发送 | 忽略计划时间，立即真实发送；打开可见浏览器；使用当前行内容与分组 |
| 串行发送 | 一个接一个发送 + 随机抖动，降低风控概率 |
| 中文日志 | 结构化中文活动日志，可一键清空（需确认） |
| 飞书机器人 | App ID/App Secret 长连接，接收精确 `0/1/2`，回复结果；任务成败/ Cookie 过期通知；三色消息窗口 |
| 可移动 | 所有运行环境与数据都在本项目文件夹内，拷贝到别的盘符可继续用 |
| 环境自检 | 首页自动检查项目路径、Python、依赖、浏览器、登录态 |

---

## 🧱 环境需求

### 运行环境
| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11（脚本基于 BAT） |
| Python | **3.10 或更高**（3.12 已实测） |
| 浏览器 | Playwright Chromium（**自动安装到项目内**，不占用用户目录） |
| 网络 | 需要能联网（安装依赖、下载 Chromium、飞书长连接、抖音登录扫码） |
| 磁盘 | 建议预留 1 GB 以上（含 Chromium） |

### 依赖清单（`requirements.txt`）
| 包 | 版本 | 用途 |
| --- | --- | --- |
| `playwright` | `>=1.54,<2` | 驱动浏览器读取会话 / 发送消息 |
| `python-dotenv` | `>=1.1,<2` | 读取本地环境变量 |
| `tzdata` | `>=2025.2` | 时区数据 |
| `Flask` | `>=3.0,<4` | 本地网页控制台 |
| `lark-oapi` | `>=1.4,<2` | 飞书官方长连接（接收 0/1/2） |

### 开发依赖（`requirements-dev.txt`）
`pytest>=8.4,<9`、`pytest-asyncio>=1.1,<2`（仅跑测试时需要）

---

## 🚀 快速开始

### 1. 安装（首次）
双击 **`install.bat`**，会自动：

1. 在项目内创建 `.venv` 虚拟环境（优先用 `runtime\python\python.exe`，否则用系统 `py -3` / `python`）
2. 安装 `requirements.txt` 依赖
3. 安装 Playwright Chromium 到 **`runtime\ms-playwright`**（项目内）

安装完成后，Python 与 Chromium 都保存在本项目里，可以整体拷贝到其他盘符使用。

> 手动安装等价命令：
> ```bat
> python -m venv .venv
> .venv\Scripts\python -m pip install -r requirements.txt
> .venv\Scripts\python -m playwright install chromium
> ```

### 2. 启动
双击 **`start.bat`**，它会：
- 自动用系统浏览器打开 `http://127.0.0.1:6161/`
- 启动本地 Flask 控制台（仅监听本机）

控制台四个页面：**首页 / 配置 / 结果 / 机器人配置**。

---

## 📖 使用指南

### 一、登录抖音
1. 打开控制台 → 首页 → 点「**扫码生成登录状态**」
2. 抖音 App 扫码确认
3. 登录态保存到 `data/storage-state.json`（以后无需重复扫码，直到过期）

### 二、读取真实会话
- 首页或配置页点「**刷新会话数据**」
- 会温和读取抖音**左侧会话栏**（低频、有上限、带冷却，避免风控），只读头像/名称/火花/摘要，**不读聊天正文**
- 结果缓存到 `data/conversations.json`

### 三、配置页使用
1. **左侧列表**：复刻抖音会话栏，显示头像、火花图标、火花天数、重燃/即将消失状态、摘要
2. **勾选**要发送的对象（默认都不勾选）
3. 点「**自动勾选火花**」一键勾选所有带火花的会话
4. **创建分组**：勾选若干人 → 输入分组名 → 「把当前勾选加入分组」
5. **添加计划**：在「消息发送列表」点「添加计划」，填写分组、每日几时几分、发送内容、发送间隔
   - 发送间隔：**只有 1 人的分组会自动忽略**；多人分组用该间隔（实际等待在设定值 ±20% 抖动）
   - 「**测试发送**」：忽略时间，立即真实发送，并打开可见浏览器，使用当前行内容与分组
   - 「**删除**」：移除该行计划
6. 勾选「**启用每日定时发送**」→ 点「**保存计划**」

### 四、发送间隔逻辑
- 分组 **1 人**：无需等待，间隔可留空
- 分组 **≥2 人**：使用该行「间隔」值；留空默认 20 秒
- 实际等待在 `0.8×~1.2×` 之间随机，范围限制在 2–300 秒

### 五、结果页
- 展示最近一次任务的成果、成功/失败会话、截图与 trace 文件

---

## 🤖 飞书机器人配置

用于：远程发送 `0/1/2` 操控控制台，并接收任务成败、Cookie 过期提醒。

### 1. 开放平台建应用
[飞书开放平台 ](https://open.feishu.cn/app)→ 开发者后台 → 创建**企业自建应用** 。
<img width="1213" height="1064" alt="tp-1" src="https://github.com/user-attachments/assets/ba54e11b-b4c4-4570-8379-97f0c3332c7a" />


### 2. 只开必要权限（最小权限）
| 用途 | 权限 |
| --- | --- |
| 接收单聊消息 | `im:message.p2p_msg:readonly` |
| 以机器人身份回复 | `im:message:send_as_bot` |
| 群聊里 @机器人（仅需群聊时） | `im:message.group_at_msg:readonly` |
<img width="2419" height="1130" alt="tp-2" src="https://github.com/user-attachments/assets/fe64e078-c9fe-413e-8394-8eeb82bdd243" />




> 通讯录 / 群成员 / 文件 / 云文档 / 日历 **不需要**，也不要添加。

### 3. 订阅事件（选长连接）
「事件与回调」→ 订阅 **接收消息 v2.0**（即 `im.message.receive_v1`），接收方式选 **长连接**。
<img width="2459" height="1153" alt="tp-3" src="https://github.com/user-attachments/assets/003d31b1-34d3-4776-8bdb-f252b5e35d32" />



- ✅ 选 **长连接** 模式（`lark-oapi` 拨出）
- ❌ 不需要填任何回调 URL / 公网 IP

### 4. 本机填写凭据
打开控制台 →「机器人配置」页：
- 填 **App ID** 与 **App Secret**
- 点「**保存并连接**」（长连接会自动建立）
- 凭据只保存在项目本地 `data/.feishu-app.local.json`，页面只显示掩码
<img width="2451" height="1224" alt="image" src="https://github.com/user-attachments/assets/602b3f2d-3804-4fd7-b2e9-6630956c3228" />
<img width="2486" height="276" alt="image" src="https://github.com/user-attachments/assets/311a9417-5cae-4e6c-b1fc-ed88b95f3cf8" />

### 5. 使用
在飞书里给机器人发：
| 指令 | 含义 |
| --- | --- |
| `0` | 返回最近 20 条本地运行日志 |
| `1` | 返回目前已启用的每日定时计划 |
| `2` | 返回当前保存的分组及成员 |

- 只接受**精确**的 `0`/`1`/`2` 单个数字
- 任务成功/失败、Cookie 连续两次过期，会自动推送到你所在会话
- 消息窗口三色区分：**左侧紫色=用户发送**、**右侧蓝色=网页发送**、**右侧绿色=系统通知**

> ⚠️ 修改 App ID/Secret 后需**重启控制台**（`lark-oapi` 长连接没有 stop 方法）。

---

## 🗂️ 目录结构

```
Fire with dy/                    # 整个项目可整体拷贝
├─ install.bat / start.bat       # 安装 / 启动
├─ run-dry.bat                   # 只验证登录与好友（不发送）
├─ send-now.bat                  # 立即发送（用 data/config.json 配置）
├─ run.py                        # 命令行发送入口（多账号/单账号）
├─ requirements.txt              # 运行依赖
├─ requirements-dev.txt          # 测试依赖
├─ app/                          # 抖音核心：浏览器、会话读取、发送、配置、多账号
├─ web/                          # Flask 本地控制台
│  ├─ server.py                  # 路由（仅本机）
│  ├─ templates/                 # 首页/配置/结果/机器人配置 页面
│  ├─ static/                    # CSS / JS
│  └─ services/                  # 配置、调度、日志、自检、飞书等
├─ runtime/                      # 【运行环境，项目内】Python 与 ms-playwright/Chromium
├─ data/                         # 【数据，项目内】
│  ├─ config.json                # 分组、计划、勾选、发送间隔、全局设置
│  ├─ conversations.json         # 会话缓存（左侧列表）
│  ├─ storage-state.json         # 抖音登录态
│  ├─ .env.local                 # HEADLESS、TRACE、DOUYIN_COOKIE 等
│  ├─ .feishu-app.local.json     # 飞书 App 凭据【机密】
│  ├─ activity-log.jsonl         # 中文活动日志
│  ├─ feishu-history.jsonl       # 飞书消息窗口历史
│  ├─ uploads/                   # 图形/表情
│  └─ assets/sparks/             # 抖音原生火花图标缓存
├─ artifacts/                    # 低层诊断：run.log、result.json、截图、trace
├─ config/                       # 多账号示例（config/accounts.json）与贴纸
└─ tests/                        # pytest 测试
```

---

## 🛠️ 维护方法

### 环境自检
打开首页 →「运行环境与文件位置」卡片，会检查：项目路径、数据目录、Python 版本、依赖、登录状态、项目内浏览器。异常时按提示操作（多数提示运行 `install.bat`）。

### 日志
- **查看**：配置页「日志信息」区（显示最近活动日志）
- **清空**：点「清空日志」→ 确认框（会同时清空 `data/activity-log.jsonl` 与 `artifacts/run.log`）
- 活动日志在 `data/activity-log.jsonl`；底层诊断在 `artifacts/run.log`（已脱敏）
- 日志/通知中**不会出现** App Secret、Cookie、Webhook 等敏感值

### 重置环境
删掉 `.venv` 与 `runtime/`，重跑 `install.bat` 即可。数据（`data/`）不受影响。

### 数据文件（都在项目内）
| 文件 | 内容 |
| --- | --- |
| `data/config.json` | 分组、计划、勾选、发送间隔、全局设置 |
| `data/storage-state.json` | 抖音登录态 |
| `data/.env.local` | `HEADLESS`、`TRACE`、Cookie 等 |
| `data/.feishu-app.local.json` | 飞书 App 凭据（机密，已被 `.gitignore` 忽略） |

### 常见问题
| 现象 | 处理 |
| --- | --- |
| 点启动没反应 / 白屏 | 确认浏览器自动打开的地址是 `http://127.0.0.1:6161/`；或手动访问 |
| 提示缺 Python | 安装 Python 3.10+，或在 `runtime\python\` 放便携版 `python.exe` |
| 提示缺环境 | 运行 `install.bat` 重建 `.venv` 与 Chromium |
| 读取会话失败/结构变化 | 可能是抖音改版；查看日志，必要时刷新页面结构选择器 |
| 抖音要求安全验证 | 按风控策略不重试；请稍后再试，避免频繁操作 |
| 飞书 0/1/2 无回复 | 确认事件订阅选了「长连接」、权限已生效、控制台在运行、本机已填凭据 |
| 改飞书凭据后无效 | 重启控制台（长连接无 stop 方法） |

---

## 🔐 安全与风控

- **仅本机**：Flask 只监听 `127.0.0.1:6161`，非本机请求直接拒绝
- **凭据本地**：App Secret、Cookie 只存在项目内，界面/日志/结果页只显示掩码或已隐藏
- **不读聊天正文**：会话读取限定在左侧会话栏
- **风控保护**：手动刷新 + 冷却、有上限的低频滚动、随机等待、串行发送、登录只重试一次、风控不重试
- **测试安全**：自动化测试不向真实抖音或飞书发送消息

> ⚠️ 请只用于**自己的抖音账号**，并遵守平台规则。工具不规避平台风控，请在合理频度内使用。
>
> 本项目仅供学习与技术交流，请勿滥用。

---

## 📄 许可证

基于 `BEER-BELLY-DU/douyin-auto-fire`，遵循其原始许可证（见 `LICENSE`）。
