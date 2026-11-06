# 使用指南

[返回首页](../README.md)

本指南适用于 Docker 和本机运行。无需单独部署 Nodes 的定时运行方式见 [GitHub Actions 指南](actions.md)。

## 配置

配置优先级为：**进程环境变量 > `.env` > `config.local.json`**。配置从当前工作目录读取，相对路径也以该目录为基准。使用 JSON 时，复制 `config.local.json.example` 后编辑。

JSON 配置适用于本机 Python 运行。默认 Docker 配置不会复制或挂载 `config.local.json`，请通过 `.env` 配置容器。

| 环境变量 | JSON 字段 | 含义 |
| --- | --- | --- |
| `MAIL_PROVIDER` | `mail_provider` | `instant_mail` 使用 Gmail；`cloud_mail` 使用原账号密码接口 |
| `CLOUD_MAIL_URL` | `cloud_mail_url` | Cloud Mail 3.0.0 站点地址，不包含 `/api` |
| `CLOUD_MAIL_EMAIL` | `cloud_mail_email` | 有权创建附属邮箱的登录邮箱 |
| `CLOUD_MAIL_PASSWORD` | `cloud_mail_password` | 登录密码 |
| `BROWSER_PATH` | `browser_path` | 可选，Chrome / Chromium 可执行文件路径 |
| `TURNSTILE_EXTENSION_PATH` | `turnstile_extension_path` | 可选，扩展目录，默认使用安装包内的 `nodes/resources/turnstile/` |
| `RESIN_URL` | `resin_url` | 可选，Resin 站点地址，不包含 `/api/v1` |
| `RESIN_ADMIN_TOKEN` | `resin_admin_token` | 可选，Resin 管理令牌，与站点地址同时填写后启用自动同步 |
| `RESIN_SUBSCRIPTION_NAME` | `resin_subscription_name` | 专用本地订阅名，默认为 `nodes` |

Docker 镜像已配置 `/usr/bin/chromium`，一般不需要指定浏览器路径。不要把宿主机的 macOS 或 Windows 浏览器路径传入 Linux 容器。

`NODES_IMAGE` 仅供 Docker Compose 选择镜像，默认是 `nodes:local`；首次运行用 `docker compose build` 构建本分支代码。它不是 Python / JSON 配置项。上游镜像不包含本分支的 Gmail 适配。

默认模板设置 `MAIL_PROVIDER=instant_mail`，固定请求 `gmail.com` 类型，不随机使用 `googlemail.com` 或其他域名，无需填写 Cloud Mail 三项配置。省略 `MAIL_PROVIDER` 时，没有 Cloud Mail 配置就用 Instant Mail；发现任意旧 Cloud Mail 配置则按旧模式校验三项字段。显式指定 `instant_mail` 可覆盖残留的旧配置。

Instant Mail 协议来自 `Instant_mail/instant_mail_api.py`。创建后立即将邮箱与 `mailbox_id` 存入 `account/instant-mail/`，并在账号记录中保留 `mail_provider`、`mailbox_id`。保留整个 `account/` 目录即可在重启后恢复。服务商分配随机 Gmail 收信地址，不支持指定完整 Gmail 地址，也不是创建 Google 账号或提供 SMTP 发信功能。验证码发送、重发使用 ProxyScrape 原接口。

Gmail 收信以本次请求前已见邮件为基线，字符串邮件 ID 映射为稳定序号；新邮件单独读取详情，按目标收件地址和 ProxyScrape 发件域名过滤，再解析文本或 HTML 中的验证码。邮箱 HTTP 请求设有 30 秒超时，失败会保留阶段信息。

Cloud Mail 客户端共用本轮登录状态，邮箱 HTTP 请求依次执行，浏览器任务仍可并发。只有明确的 HTTP 401 或 API `code: 401` 才触发一次重新登录。网络错误、权限错误、登录失败或第二次 401 会直接报告。

Cloud Mail 新邮箱使用站点返回的第一个可用域名。两种邮箱均只处理新增、收件人匹配且发件人来自 ProxyScrape 域名的邮件。

## Docker

```bash
# 首次运行或更新版本后构建本分支镜像
docker compose build

# 检查邮箱，不创建账号
./scripts/run-docker.sh --check-mail

# 一个任务，只尝试一次
./scripts/run-docker.sh --count 1 --threads 1 --attempts 1 --hide-browser

# 继续已保存且邮箱已验证的账号
./scripts/run-docker.sh --resume saved@example.org

# 查看全部参数
./scripts/run-docker.sh --help
```

`scripts/run-docker.sh` 会转发参数，等待容器结束，然后将账号和节点复制到运行脚本的本机项目目录。输出先复制到临时目录，再写入本机目录，以免 Docker 共享同一目录时边读边覆盖文件。

复制完成后删除本次运行容器，并保留原任务退出码。复制失败时不删除容器，终端会显示容器名、临时目录和手动取回命令。例如，将日志里的实际容器名替换到下方：

```bash
docker cp CONTAINER_NAME:/app/node/. ./node/
docker cp CONTAINER_NAME:/app/account/. ./account/
```

不要在取回文件前删除保留的容器。Compose 的绑定目录属于 Docker 所在主机；在 Colima 或远程 Docker 下，它可能与本机项目目录不同。将本机备份迁移到其他 Docker 主机后，需要先将账号文件放入该主机映射的 `account/` 目录，`--resume` 才能读取。

容器使用 Chromium 和 Xvfb，无需宿主机桌面。GHCR 的版本镜像同时包含 `linux/amd64` 和 `linux/arm64`，Docker 根据机器架构自动选择，不需要手动添加 `--platform`。

发布标签包括完整版本（例如 `0.4.0`）、次版本（`0.4`）和最新稳定版（`latest`）。`latest` 只随稳定版本发布更新，不跟随 `main` 的每次提交。

本地修改源码后，可构建独立的镜像：

```bash
NODES_IMAGE=nodes:local docker compose build
NODES_IMAGE=nodes:local ./scripts/run-docker.sh --check-mail
```

## Linux 本机

需要 Python 3.11+、Chromium 和 Xvfb。Debian / Ubuntu 安装示例：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv chromium xvfb xauth
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/nodes --check-mail
.venv/bin/nodes --count 1 --threads 1 --attempts 1 --hide-browser
.venv/bin/nodes --resume saved@example.org
```

先按[首页](../README.md#docker--本机运行)下载项目、复制 `.env.example` 并填写邮箱配置，再在该工作目录运行；账号和节点也写入此目录。

Linux 没有 `DISPLAY` 时，`nodes` 在注册前自动调用 `xvfb-run`，无需额外启动脚本。检查邮箱和恢复已有账号不启动显示器。有桌面的 Linux 可以使用 `--show-browser`。在 Xvfb 中，该参数只会把窗口显示在虚拟显示器中。

如果 Chromium 由 Snap 安装或位于非标准路径，可配置 `BROWSER_PATH` 指向实际可执行文件。

## macOS 与 Windows 本机

需要 Python 3.11+ 和 Chrome。macOS 安装并运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/nodes --check-mail
.venv/bin/nodes --count 1 --threads 1 --attempts 1 --show-browser
```

Windows PowerShell 使用相同的安装入口：

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\nodes.exe --check-mail
.venv\Scripts\nodes.exe --count 1 --threads 1 --attempts 1 --show-browser
```

两种平台都需要先在工作目录配置 `.env`。Docker 启动脚本需要 POSIX shell，Windows 推荐使用 WSL。

在可交互终端中，不带参数运行 `nodes` 会进入菜单。也可用当前 Python 环境执行 `python -m nodes`。非交互环境必须指定 `--count`、`--resume`、`--check-mail` 或 `--sync-resin`。macOS 和 Windows 完整注册流程尚未实机验收。

## 命令参数

| 参数 | 作用 | 默认值 |
| --- | --- | --- |
| `--count N` | 本轮任务数，失败任务可能按 `--attempts` 使用新邮箱重试 | 交互菜单输入 |
| `--resume EMAIL` | 继续该邮箱已保存账号的试用处理和代理导出，只执行一次 | 关闭 |
| `--check-mail` | Instant Mail 创建并保存 Gmail 邮箱、读取邮件列表；Cloud Mail 登录并读取主邮箱 | 关闭 |
| `--sync-resin` | 只将已有导出合并同步到 Resin，不创建邮箱、注册或领取试用 | 关闭 |
| `--threads N` | 并发数，实际不超过任务数和 8 | 3 |
| `--attempts N` | 每个任务最多尝试次数 | 3 |
| `--hide-browser` | 隐藏浏览器窗口 | 开启 |
| `--show-browser` | 显示浏览器窗口 | 关闭 |
| `--mail-timeout N` | 等待新验证邮件的秒数 | 180 |
| `--challenge-timeout N` | 浏览器验证超时秒数 | 120 |

`--count`、`--resume`、`--check-mail`、`--sync-resin` 互斥。`--resume` 不启动浏览器、不注册新账号；它要求已保存的 ProxyScrape 访问凭据仍然有效。Instant Mail 账号若尚未完成验证，会使用记录里的 `mailbox_id` 恢复同一邮箱，重新发送验证码并等待新邮件，遵守 `--mail-timeout`；已验证账号直接导出。Cloud Mail 旧账号仍要求已验证。`--sync-resin` 只需要 Resin 配置和已有节点文件。

一轮命令行任务结束后退出。全部任务导出且已启用的 Resin 同步成功返回 0；存在未完成任务或同步失败返回 1；参数错误返回 2；程序捕获到键盘中断时返回 130。

## 文件与恢复

| 文件 | 内容 |
| --- | --- |
| `account/accounts_*.jsonl` | 每行一份账号记录，包含登录和代理凭据、验证状态、导出数量以及错误信息 |
| `node/proxies_*.txt` | 本轮成功导出的 HTTP 代理，每行一个地址 |
| `node/proxies_resume_*.txt` | 继续已有账号后生成的新代理文件 |

每轮注册使用独立的时间戳文件。已完成注册的账号即使后续失败也会保存。`--resume` 从账号文件中查找该邮箱最新记录，处理结果追加到原文件，保留原记录。

只有服务端确认 `EmailVerified: true` 后才会处理试用。尚未领取时提交一次领取请求；已经领取时从套餐概览中识别试用子账号，不选择付费子账号替代。服务端拒绝、套餐过期或代理列表暂不可用时，命令会报错。可以根据错误修复后，再对同一账号执行 `--resume`。

如果领取请求发出后没有收到响应，不会立即重复提交。再次 `--resume` 时会先重新查询领取状态。

## Resin 自动导入

支持 Resin 1.2.0 的本地订阅接口。在 `.env` 中配置：

```dotenv
RESIN_URL=https://resin.example.org
RESIN_ADMIN_TOKEN="你的管理令牌"
RESIN_SUBSCRIPTION_NAME=nodes
```

使用管理令牌，不是代理请求用的 `RESIN_PROXY_TOKEN`。地址和令牌均留空时不连接 Resin；只填写其中一项会报配置错误。`--check-mail` 不执行同步。

配置后，批量任务结束会统一同步一次，`--resume` 导出成功后也会同步。程序读取当前工作目录下所有 `node/proxies_*.txt`（包含恢复导出），与 Resin 同名专用订阅中的已有节点合并，按完整代理地址去重。首次创建 `local` 订阅，后续只更新其内容并触发刷新，不修改其他订阅或平台配置。多账号仍共用这一个订阅。

已有同名订阅必须是逐行 HTTP 代理文本的本地订阅；远程订阅、其他格式或多个同名订阅会报错，请更换专用订阅名。导入成功表示 Resin 已解析节点，可用性仍由 Resin 后续探测。

同步失败不会重新注册，也不会删除账号或节点文件。修复配置或网络后，单独补同步：

```bash
./scripts/run-docker.sh --sync-resin
# 本地 Python 安装也可运行：
.venv/bin/nodes --sync-resin
```

Docker 同步读取 Docker 主机映射的 `node/`；使用远程 Docker 或 Colima 时，参见上面的目录映射说明。Resin 内容已保存但响应丢失时，重试会先查询同名订阅并合并，避免重复创建。

合并会保留历史节点，不自动判断套餐到期或删除旧节点。需要清理时，同时移走对应的本地导出文件并从 Resin 订阅内容中移除，避免下次同步重新加入。不要同时运行多个进程向同一订阅同步：Resin 的内容更新接口不提供原子追加，并行更新可能互相覆盖。

接口依据：[Resin 订阅接口](https://github.com/Resinat/Resin/blob/v1.2.0/internal/service/control_plane_subscription.go)。

## 可选的代理检查

仓库保留 `tools/check_proxies.py`。它默认读取最新代理文件，并尝试访问 xAI 登录页面；这只说明该次请求的结果，不能代表所有目标站点均可使用。

```bash
.venv/bin/python -m pip install '.[tools]'
.venv/bin/python tools/check_proxies.py --help
# --url 可改为你有权测试的目标；下面仅检查前 3 个代理。
.venv/bin/python tools/check_proxies.py --limit 3 --workers 1 --url https://example.com
```

该工具会请求真实目标，并在日志中输出代理地址和认证凭据。分享日志前请删除凭据。

## 更新

```bash
git pull --ff-only
docker compose build
```

固定了 `NODES_IMAGE` 版本的用户，需要先修改该值再拉取新版本。本地源码构建用户重新执行 `NODES_IMAGE=nodes:local docker compose build`。本机直接运行时，在更新后重新执行 `.venv/bin/python -m pip install .`。

保留 `.env`、`account/` 和 `node/`，不要把它们当作可删除的构建缓存。

## 常见问题

**本机没有节点文件？** 使用 `./scripts/run-docker.sh ...`，按终端显示的本机路径查找。直接使用 Compose 绑定目录时，文件所在主机取决于 Docker 的文件共享配置，参见上面的 [Docker](#docker) 说明。

**Cloud Mail 返回 401？** 程序会重新登录并重试当前请求一次。仍然失败时，检查登录信息和账号接口权限，日志会保留请求方法和接口路径。

**缺少子账号或没有代理？** 注册响应不一定包含试用子账号。程序会查询试用状态；已注册账号使用 `--resume EMAIL`，避免为重试导出重复注册。

**Turnstile 验证超时？** 查看日志中的输入框、iframe 和验证凭据状态，并确认网络能访问目标站点及验证服务。超时不代表注册成功。
