# AI SSH MCP

这是一个本地 MCP 工具服务，用来让其他 agent 以受控方式连接一台 Linux/BusyBox 嵌入式设备。它保留一个 MCP server，内部按能力分成 `core`、`diag`、`files`、`interactive`、`policy`、`maint` 多类工具。

当前工具名全部使用前缀式接口；旧工具名不再注册。

## 功能分组

- `core_configure_device`：保存单台设备的 host、port、username，并把 SSH 密码和 su 密码存入本机 keyring。
- `core_test_connection`：验证 SSH 登录、`su -` 提权和命令执行。
- `core_list_recent_runs` / `core_get_run_detail`：查看审计记录。
- `diag_list_whitelist` / `diag_add_whitelist` / `diag_delete_whitelist`：管理诊断命令白名单。
- `diag_plan_task` / `diag_run_plan`：先生成只读诊断计划，再确认执行。
- `diag_run_shell`：执行低风险普通 shell 查询命令，并返回输出和退出码。
- `files_download`：通过 SFTP over SSH 将设备文件下载到本机指定目录。
- `interactive_run_tool`：进入指定目录启动交互式后台工具，输入指令并读取提示符前的多行结果。
- `policy_evaluate_operation`：独立评估某类操作的风险和审批决策。
- `maint_plan_change` / `maint_apply_change`：两阶段维护操作，先生成计划，再确认执行。
- `maint_runbook`：运行本地预定义维护 runbook。

## 安装

```powershell
cd <PROJECT_ROOT>
py -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\python -m pip install -e .
```

如果你的系统没有 `py`，用 Python 的完整路径执行同样命令即可。

## opencode 配置

项目把 MCP 和 OpenCode agent 分开开放：

- MCP 服务代码在 `src/ai_ssh_mcp/`，通过 `python -m ai_ssh_mcp` 启动。
- OpenCode 维护 agent 在 `.opencode/agents/device-maintainer.md`，只描述 agent 行为和权限。
- MCP 启动配置放在 `opencode.json`，不要写进 agent 文件。

仓库提供了一个不含本机路径和密码的示例：

```text
opencode.example.json
```

如果你使用项目级 OpenCode 配置，可以复制成项目内的 `opencode.json` 后按本机环境调整；如果你使用全局配置，全局配置文件通常在：

```text
C:\Users\<你的用户名>\.config\opencode\opencode.json
```

示例内容：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "ai_ssh_device": {
      "type": "local",
      "command": [
        "python",
        "-m",
        "ai_ssh_mcp"
      ],
      "cwd": ".",
      "enabled": true,
      "timeout": 10000
    }
  }
}
```

如果全局 `opencode.json` 不是从项目目录启动，请把 `cwd` 改成你的项目绝对路径，并把 `command` 里的 `python` 改成你的虚拟环境 Python 路径，例如 `.venv\Scripts\python.exe`。

也可以用：

```powershell
opencode mcp add
opencode mcp list
```

修改 MCP 代码或重新安装后，重启 opencode 才能看到最新工具列表。

## opencode 维护 Agent

项目内提供了一个 OpenCode subagent：

```text
.opencode/agents/device-maintainer.md
```

它用于设备维测和维护操作。日常可以先继续使用普通 OpenCode AI；当你希望进入维护流程时，可以明确说：

```text
用 device-maintainer 维护 agent，帮我检查设备网络问题，先给执行方案。
```

这个维护 agent 的固定流程是：先理解你的维护想法，必要时做只读诊断，然后输出执行方案。方案必须展示 `plan_id` 或 runbook 名称、执行步骤、验证步骤、回滚/恢复步骤、风险等级，以及是否还需要最终人工确认。

你可以继续沟通修改方案，例如：

```text
这个方案第 2 步改成先看最近 50 行日志。
```

最终确认时可以说：

```text
我确认按这个最终方案执行。
```

确认后，agent 会自动执行该方案内的步骤，不再逐条命令确认。这里的“自动执行”不是无条件运行任意命令：必须先有执行方案、用户最终确认，并且 MCP 校验方案 ID、命令哈希和策略通过。

## 常用对话示例

首次配置设备：

```text
use ai_ssh_device。调用 core_configure_device，配置我的嵌入式设备：
host 是 <DEVICE_IP>，port 是 22，username 是 <DEVICE_USER>。
ssh_password 和 su_password 我会提供。
```

测试连接：

```text
use ai_ssh_device。调用 core_test_connection，测试设备 SSH 登录和 su 是否正常。
```

生成诊断计划：

```text
use ai_ssh_device。帮我检查设备网络为什么不通。
先调用 diag_plan_task，把计划和命令展示给我，不要直接执行。
```

确认后执行诊断计划：

```text
我确认执行 approval_id 为 xxx 的计划。
use ai_ssh_device 调用 diag_run_plan，user_confirmed=true。
```

执行普通 shell 查询：

```text
use ai_ssh_device。调用 diag_run_shell：
commands = ["pwd", "cd /var/log", "ls -l", "cd /path/not-exist"]
```

`diag_run_shell` 是低风险诊断工具，策略默认允许只读/安全查询。返回里每条命令都有 `output/stdout`、`stderr`、`exit_status`、`success`、`duration_ms` 和 `truncated`。

下载设备文件：

```text
use ai_ssh_device。调用 files_download：
remote_paths = ["/var/log/messages", "/tmp/debug.log"]
local_dir = "C:\\Users\\olivi\\Downloads\\device_logs"
user_confirmed = true
```

运行交互式后台工具：

```text
use ai_ssh_device。调用 interactive_run_tool：
work_dir = "/path/to/tool"
tool_command = "./tool_name"
inputs = ["show status", "show detail"]
prompt_pattern = "xxx>$"
prompt_settle_seconds = 0.8
user_confirmed = true
```

维护服务重启计划：

```text
use ai_ssh_device。调用 maint_plan_change：
change_type = "service_restart"
target = "network"
```

确认后执行维护计划：

```text
我确认执行 plan_id 为 xxx 的维护计划。
use ai_ssh_device 调用 maint_apply_change，user_confirmed=true。
```

执行预定义 runbook：

```text
use ai_ssh_device。先调用 maint_runbook：
name = "restart_network"
user_confirmed = false
```

确认方案后执行同一个 runbook 计划：

```text
我确认按这个最终 runbook 方案执行。
use ai_ssh_device 调用 maint_runbook：
name = "restart_network"
plan_id = "上一步返回的 plan_id"
user_confirmed = true
```

## 策略和维护

`policy_evaluate_operation` 会返回：

```json
{
  "decision": "allow | deny | require_user_confirmation",
  "risk_level": "low | medium | high",
  "reason": "...",
  "matched_rules": [...]
}
```

默认策略：

- 低风险诊断查询：允许。
- 文件下载、交互式工具：中风险，需要确认。
- 维护操作、服务重启、设备重启：高风险，需要确认。
- 读取敏感路径或明显危险命令：拒绝。

维护能力采用两阶段：

- `maint_plan_change` 只生成计划，不连接设备。
- `maint_apply_change` 只执行已保存、哈希未篡改、用户确认的维护计划。
- `maint_runbook` 只读取本机 `runbooks/` 里的预定义 JSON runbook，不接受临时拼接危险命令；执行时必须带上已经展示给用户的 `plan_id`。

runbook 文件放在本地状态目录的 `runbooks/` 下，不提交到 Git。

## 安全边界

- 诊断白名单和自定义命令会经过危险命令扫描。
- 执行计划保存命令哈希，执行前会再次校验。
- 文件下载只接受明确的绝对文件路径，不支持通配符，不下载目录。
- 文件下载会拒绝 `/etc/shadow`、`.ssh/`、`ssl/private/`、`/proc/kcore` 等敏感路径。
- 交互式工具会等待提示符出现后再等待一段静默时间，确认输出结束。
- 维护类动作必须经过策略判断；高风险动作必须 `user_confirmed=true`。

## 本地测试

```powershell
.\.venv\Scripts\python -m unittest discover
```

没有真实设备时，本地测试仍覆盖配置、凭据键名、白名单、审批哈希、输出截断、下载路径、交互式输出、策略判断、维护计划和 MCP 注册。
