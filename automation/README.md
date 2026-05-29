# Sidecar Automation Skeleton

This folder is separate from the existing GUI scripts. The manual local flow
through `newkami.py` and `newtkmain.py` remains unchanged.

## Code Split

Use two deployment packages:

- Central controller package: `central_server.py`, `storage.py`, `job_types.py`.
- Local worker package: `worker.py`, `license_guard.py`, `job_types.py`.

The central controller runs on one server or one always-on PC. Local workers run
on each Bit Browser machine. Workers poll the controller for jobs and report
results back.

## Licensing And Packaging

The local worker has a `LicenseGuard` adapter that calls the existing
`FnKuaiYanGoBasedAPI` card-key login without launching the GUI. During
development, keep `require_license: false`. Before production packaging, set:

```yaml
require_license: true
card_number: "your-card-key"
```

For packaging, keep the controller and worker as separate executables. Protect
only the local worker package with code obfuscation/PyInstaller/Nuitka and the
existing card-key heartbeat. The controller can stay private on your own server,
so it does not need to be distributed to client machines.

## First Local Test

Start the controller:

```powershell
python automation/central_server.py --host 127.0.0.1 --port 8766 --token test-token
```

Copy the example worker config:

```powershell
Copy-Item automation/config_example.yaml automation_config.yaml
```

Edit `automation_config.yaml`:

```yaml
central_token: test-token
require_license: false
```

Start the worker:

```powershell
python automation/worker.py --config automation_config.yaml
```

Create a test comment draft job:

```powershell
python automation/create_job.py --token test-token --job-type comment_draft --payload-json "{\"account_id\":\"demo\",\"tweet_text\":\"AI tools are changing daily workflows.\"}"
```

The worker writes drafts to `automation/output/comment_drafts.jsonl`.

## Multi-PC And Account Routing

Each worker has a stable `node_id`. Add Bit Browser group IDs to each local
worker config:

```yaml
node_id: PC-01
sync_group_ids:
  - "your-bit-group-id"
```

The worker calls the local Bit Browser API and uploads profile records to the
controller. The controller stores:

```text
node_id -> group_id -> profile_id
```

List synced accounts:

```powershell
python automation/accounts_cli.py --token test-token list-accounts --group-id your-bit-group-id
```

Create Grok-plan jobs for every synced profile in a group:

```powershell
python automation/accounts_cli.py --token test-token create-grok-plan-jobs --group-id your-bit-group-id --period weekly
```

This is the same routing layer the Discord bot will use later. One bot is
enough because it talks to the central controller, and the controller routes
jobs to the correct `node_id`.

## X Built-in Grok Collection

Grok collection is off by default during development:

```yaml
enable_grok_browser: false
```

When enabled, the worker opens the target Bit Browser `profile_id`, navigates to
the X built-in Grok URL, submits the generated planning prompt, and returns the
raw Grok response to the controller:

```yaml
enable_grok_browser: true
grok:
  grok_url: https://x.com/i/grok
```

The controller stores successful Grok responses as `account_plans` with
`status=draft`. View drafts:

```powershell
python automation/accounts_cli.py --token test-token list-plans --group-id your-bit-group-id --status draft
```

If X changes the page, adjust the selectors in `automation_config.yaml` under
`grok.input_selectors`, `grok.send_selectors`, and `grok.response_selectors`.

## Discord Bot

Copy the example config:

```powershell
Copy-Item automation/discord_config_example.yaml discord_config.yaml
```

Edit:

```yaml
discord_token: "your-discord-bot-token"
central_api: http://your-controller:8766
central_token: change-me
```

Run:

```powershell
python automation/discord_bot.py --config discord_config.yaml
```

Supported commands:

```text
!version
!help
!status
!groups
!bind test your-bit-group-id
!accounts test
!plan weekly test
!plan daily test
!plan list test
!plan draft test
!plan schedule test
!plan approve 123
!mode1 test
!mode2 test url=https://x.com/xxx/status/123
!mode3 test
!jobs
!job detail 123
!logs 123
!schedule test
@Bot A组 绑定 测试 your-bit-group-id
```

One Discord bot is enough. It talks only to the central controller; the
controller routes jobs to workers by `node_id` and accounts by Bit Browser
`profile_id`.

## Scheduler

The scheduler runs on the central side. It dispatches due `scheduled_tasks` into
normal queued jobs that workers can pick up.

Copy the example config:

```powershell
Copy-Item automation/scheduler_config_example.yaml scheduler_config.yaml
```

Run once:

```powershell
python automation/scheduler.py --config scheduler_config.yaml --once
```

Run continuously:

```powershell
python automation/scheduler.py --config scheduler_config.yaml
```

Approve a draft plan from CLI:

```powershell
python automation/accounts_cli.py --token test-token approve-plan --plan-id 123
```

View scheduled tasks:

```powershell
python automation/accounts_cli.py --token test-token list-schedule --group-id your-bit-group-id
```

## Safety Rails

- The worker uses `automation_config.yaml`, not the GUI `config.yaml`.
- The worker creates local profile locks under `automation/local_locks`.
- The worker has an enable switch: `worker_enabled: false`.
- The current skeleton only implements planning and draft jobs. Browser/Grok
  adapters should be added behind explicit job types after review.
