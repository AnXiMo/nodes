# GitHub Actions 手动运行

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
| `NODES_ACCOUNT_BACKUP_KEY` | 必需：Fernet 加密密钥，用于长期保存账号、密码、会话和邮箱恢复资料 |

也可以通过 GitHub CLI 交互输入，例如 `gh secret set CLOUD_MAIL_PASSWORD --repo 你的用户名/nodes`。不要将凭据写进工作流或提交 `.env`。

默认合并到 Resin 的 `nodes` 专用本地订阅。修改名称时，在同一设置页的 **Variables** 中添加 `RESIN_SUBSCRIPTION_NAME`，详见 [Resin 同步规则](usage.md#resin-自动导入)。已有订阅的节点会保留并去重。

设置 `MAIL_PROVIDER=instant_mail`。任务使用运行表单的数量输入；当前已移除 `schedule`，不会自动运行。浏览器由固定提交版本的 setup-chrome Action 安装 Chrome for Testing，并通过 `BROWSER_PATH` 传入应用与浏览器检查。

生成密钥可运行 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`，将结果填入同名 Secret，并另存到自己的密码管理器。密钥不能提交到仓库，也不能与加密备份一同公开；丢失密钥将无法恢复历史备份。已有备份时不要直接替换密钥，应先用旧密钥解密并安全迁移。

## 首次检查与手动运行

1. 打开仓库 **Actions → Daily Nodes**。Fork 后如提示工作流未启用，先启用工作流。
2. 点击 **Run workflow**，保留勾选“仅检查运行环境”，执行一次环境检查。默认无需 Secrets，只验证安装、命令入口、虚拟显示器和本地页面中的浏览器扩展，不访问邮箱、ProxyScrape 或 Resin。配置加密密钥后，可同时开启 `check_backup`，验证远端备份读写及新目录解密恢复；首次会创建空的加密资料库，不创建或登录账号。
3. 配置好 Secrets 后，取消勾选“仅检查运行环境”，数量保留为 `3`，再手动运行一次实际任务。

也可以先保留“仅检查运行环境”，同时勾选“另外检查 Gmail 邮箱及 Resin 连接”。这会创建一个 Gmail 收信地址并检查邮件列表，使用管理令牌读取 Resin 订阅列表；不注册 ProxyScrape、不发送验证码、不写入 Resin 订阅。

环境检查通过不代表真实注册一定成功。浏览器验证、试用资格和代理数量仍取决于外部服务；实际任务日志会保留失败阶段。

缺少必需的 Secret 时，任务会在注册前报出配置名称。任务成功后，节点自动合并到 Resin，无需下载文件再导入。

## 执行规则

- 仅手动触发；当前定时执行已关闭。
- 表单默认3个账号，首次验证可填写1；串行执行，每个任务只尝试一次。实际成功数量以日志为准。
- 批量任务结束后统一合并同步 Resin。部分账号失败时，其余成功导出的节点仍会同步，整个作业保持失败状态。
- 同一仓库的每日任务串行运行，避免定时任务和手动任务互相覆盖订阅。
- 手动实际运行可调整数量；作业最长运行30分钟。

GitHub 定时任务可能延迟，高负载时可能丢弃排队任务；公开仓库连续 60 天无活动时，定时工作流会被自动停用。21:00 是计划触发时间，并非准点保证。[GitHub 官方说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

以后需要定时运行时，再向 [`.github/workflows/daily.yml`](../.github/workflows/daily.yml) 显式添加 `schedule`；当前仅保留 `workflow_dispatch`。

## 输出与失败处理

每次提交注册请求前，程序使用同一个 HTTP 会话向 `api.ipify.org` 探测公网 IP，
并在对应 worker 日志打印 `注册前出口探测 IP：…`。探测设置3秒连接、5秒读取超时；
失败、重定向或无效响应显示“未知”，继续原注册流程，不打印响应正文或代理凭据。
这是探测站看到的即时出口，不能证明注册服务看到相同 IP，也不保证不同账号的出口唯一。
程序不会为此切换代理或重新注册账号；历史未记录的注册 IP 也无法补推。

工作流把账号 JSONL（含网站登录密码、访问会话、代理凭据）和 Instant Mail 的 `mailbox_id` 用 Fernet 认证加密，写入独立的 **`account-vault` 分支 / `accounts.vault`**。分支只含密文，Git 历史保留历次备份，不依赖 Actions artifacts 的保留期。即使仓库公开，也不得上传明文 `account/`、`node/` 或密钥。

每次实际任务先恢复备份。注册接口成功后立即保存密码和会话，随后每次账号记录更新都上传加密检查点；任务失败后也尝试最终保存。密钥缺失、解密失败、旧记录被删改或分支并发更新会使任务失败，不覆盖已有备份。任务被强制终止或 GitHub 长时间不可达时，尚未上传的最后一次记录仍可能丢失；应检查备份步骤的结果。

## 恢复已有账号

在 **Run workflow** 中关闭 `check_only`，开启 **`recover_accounts`**，可选填写 **`recover_email`**；留空会依次恢复所有保存的账号。恢复模式不会注册账号。可用会话直接继续导出，账号 API 返回 401 时用保存的网站密码重登一次；成功后先保存新会话，再尝试导出并同步 Resin。若登录需要页面验证，会打开官方登录页的原生浏览器验证，失败后停止。

尚未完成验证的 Instant Mail 账号沿用已有邮箱资料并重新请求验证码。已验证账号无需邮件服务。错误密码、账号限制、套餐过期或服务端拒绝会保留原记录并报错；重新登录不会延长套餐。批量恢复中单个账号失败会继续处理后续账号，整体运行标记失败；本次成功导出的节点仍会统一同步。

旧版本已经销毁的账号文件和密码不能由本功能找回。只有代理用户名/代理密码不足以登录网站；知道原网站密码时，可在本机运行 `nodes --login EMAIL` 导入既有账号，再使用加密导入/备份流程。下载 `accounts.vault` 并通过 `nodes --restore-accounts FILE` 可恢复到空目录，详见[使用指南](usage.md#账号密码保留与重登)。

Resin 中保留的是已成功同步的节点；该流程不会自动删除历史或到期节点。每次任务在全新的运行环境开始，本轮节点会与 Resin 中的已有内容合并。
