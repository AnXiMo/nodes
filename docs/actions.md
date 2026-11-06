# GitHub Actions 每日运行

[返回首页](../README.md)

无需为 Nodes 单独准备服务器或 Docker。工作流使用 GitHub 的 Ubuntu 运行环境，直接安装 Python 依赖并运行 Chrome / Chromium；Resin 本身仍需要保持在线。

## 配置

默认使用 Instant Mail Gmail，无需邮箱 Secrets。在你自己的仓库中打开 **Settings → Secrets and variables → Actions → Secrets**，添加 Resin 的两项 Repository secrets。若需要原 Cloud Mail，在 Variables 中设置 `MAIL_PROVIDER=cloud_mail`，再补充三项 Cloud Mail Secrets：

| Secret | 内容 |
| --- | --- |
| `CLOUD_MAIL_URL` | 仅 Cloud Mail：3.0.0 站点地址，不包含 `/api` |
| `CLOUD_MAIL_EMAIL` | 仅 Cloud Mail：有权创建附属邮箱的登录邮箱 |
| `CLOUD_MAIL_PASSWORD` | 仅 Cloud Mail：邮箱登录密码 |
| `RESIN_URL` | Resin 站点地址，不包含 `/api/v1`，需要能从 GitHub 运行环境访问 |
| `RESIN_ADMIN_TOKEN` | Resin 管理令牌，不是代理请求用的令牌 |

也可以通过 GitHub CLI 交互输入，例如 `gh secret set CLOUD_MAIL_PASSWORD --repo 你的用户名/nodes`。不要将凭据写进工作流或提交 `.env`。

默认合并到 Resin 的 `nodes` 专用本地订阅。修改名称时，在同一设置页的 **Variables** 中添加 `RESIN_SUBSCRIPTION_NAME`，详见 [Resin 同步规则](usage.md#resin-自动导入)。已有订阅的节点会保留并去重。

可设置 `MAIL_PROVIDER=instant_mail` 和 `NODES_COUNT=3`。定时任务读取 `NODES_COUNT`（默认3）；手动任务使用运行表单的数量输入。浏览器由固定提交版本的 setup-chrome Action 安装 Chrome for Testing，并通过 `BROWSER_PATH` 传入应用与浏览器检查。

## 首次检查与手动运行

1. 打开仓库 **Actions → Daily Nodes**。Fork 后如提示工作流未启用，先启用工作流。
2. 点击 **Run workflow**，保留勾选“仅检查运行环境”，执行一次环境检查。此模式无需 Secrets，只验证安装、命令入口、虚拟显示器和本地页面中的浏览器扩展，不访问邮箱、ProxyScrape 或 Resin。
3. 配置好 Secrets 后，取消勾选“仅检查运行环境”，数量保留为 `3`，再手动运行一次实际任务。

也可以先保留“仅检查运行环境”，同时勾选“另外检查 Gmail 邮箱及 Resin 连接”。这会创建一个 Gmail 收信地址并检查邮件列表，使用管理令牌读取 Resin 订阅列表；不注册 ProxyScrape、不发送验证码、不写入 Resin 订阅。

环境检查通过不代表真实注册一定成功。浏览器验证、试用资格和代理数量仍取决于外部服务；实际任务日志会保留失败阶段。

缺少必需的 Secret 时，任务会在注册前报出配置名称。任务成功后，节点自动合并到 Resin，无需下载文件再导入。

## 每日执行规则

- 每天北京时间 **21:00** 运行，工作流对应 UTC `13:00`。
- 尝试 **3 个账号**，串行执行，每个任务只尝试一次。实际成功数量以日志为准。
- 批量任务结束后统一合并同步 Resin。部分账号失败时，其余成功导出的节点仍会同步，整个作业保持失败状态。
- 同一仓库的每日任务串行运行，避免定时任务和手动任务互相覆盖订阅。
- 手动实际运行可调整数量；定时任务默认3，可用 `NODES_COUNT` 变量修改。作业最长运行30分钟。

GitHub 定时任务可能延迟，高负载时可能丢弃排队任务；公开仓库连续 60 天无活动时，定时工作流会被自动停用。21:00 是计划触发时间，并非准点保证。[GitHub 官方说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

需要修改每日时间或数量时，编辑 [`.github/workflows/daily.yml`](../.github/workflows/daily.yml) 中的定时表达式和数量默认值。停止每日任务可在 Actions 的工作流菜单中选择 **Disable workflow**。

## 输出与失败处理

按本项目的每日运行方式，**只同步 Resin，不保存账号或节点附件**。账号和节点仅在本次作业的临时目录中产生，不提交到 Git，也不上传为 Actions artifacts；运行环境销毁后不再保留这些文件。

因此，Resin 同步失败后无法从已结束的作业取回文件。重新运行实际任务会注册新账号，不会恢复上一次注册结果。若需要保存账号、手动恢复或长期管理本地文件，请使用 [Docker / 本机运行](usage.md)。

Resin 中保留的是已成功同步的节点；该流程不会自动删除历史或到期节点。每次任务在全新的运行环境开始，本轮节点会与 Resin 中的已有内容合并。
