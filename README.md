# Website Healthcheck — Python Website Healthcheck Exercise

English | [简体中文](README_CN.md) · [MIT License](LICENSE)

Website Healthcheck is a single-machine Python engineering exercise combining HTTP checks and retries, SQLite execution records, missed-execution and interruption detection, Gmail summary alerts, and pytest / GitHub Actions validation. Each invocation executes only the current round; system cron starts the next round five minutes later by default.

Start with [installation and tests](#installation-and-first-run) to explore the project without real website or Gmail access. This exercise demonstrates module boundaries, persistent execution and notification state, process coordination, and isolated tests. It is not production-ready; see [current limitations](#current-limitations-and-production-preparation). Detailed documents under `docs/` are currently in Chinese. The [original exercise requirements](refs/requirement.md) are retained as historical context; their short-README limit no longer applies to this showcase.

## Contents

- [Features and check rules](#features-and-check-rules)
- [Technology stack](#technology-stack)
- [Directory layout and module responsibilities](#directory-layout-and-module-responsibilities)
- [How one round runs](#how-one-round-runs)
- [Installation and first run](#installation-and-first-run)
- [Migration from the previous name](#migration-from-the-previous-name)
- [Configuration](#configuration)
- [Deployment with a five-minute schedule](#deployment-with-a-five-minute-schedule)
- [Tests and continuous integration](#tests-and-continuous-integration)
- [Releases](#releases)
- [Data maintenance and troubleshooting](#data-maintenance-and-troubleshooting)
- [Current limitations and production preparation](#current-limitations-and-production-preparation)

## Features and check rules

The default targets, `https://canlah.ai/` and `https://canlah.ai/playbook/`, are examples retained from the original exercise. URLs and check rules can be changed through configuration. Each page must meet all three conditions:

| Check | Default passing condition |
| --- | --- |
| Status | The final HTTP status after following redirects is 200 |
| Response time | At most 5 seconds from sending the request until the full response body has been read, including redirects |
| Content | The response text contains case-sensitive `Canlah`; JavaScript is not executed and the DOM is not parsed |

A failure in any condition triggers retries: by default, up to 2 additional attempts with a 2-second delay, for at most 3 requests. If one target ultimately fails, the other targets are still checked. Recovery during retries preserves the failed attempts without creating a final-failure alert. The command exits with a nonzero status if any target ultimately fails.

HTTP waiting timeouts are separate from the 5-second check threshold. `request_timeout_seconds` defaults to 10 seconds and limits HTTPX's individual waiting phases; it is not a deadline for the whole round. Once the full response arrives, its measured duration is compared against the 5-second threshold.

The project also provides:

- Execution records: start and finish times, execution states, final page results, and every actual attempt.
- Missed-execution detection: on the next successful startup, identify past slots that never started, record them, and report them without replaying historical requests.
- Interruption detection: after confirming that an old instance has stopped, mark its unfinished round as interrupted. A new slot first verifies and stops a stuck older instance before executing the current round.
- Summary emails: after all current checks finish, report unnotified failures, missed slots, interruptions, and the latest states of all recorded targets. Later recovery does not discard historical incidents.
- Notification records: mark the reported incidents only after successful sending. Keep failed deliveries pending and retry after the configured minimum mail interval.

## Technology stack

| Area | Choice and purpose |
| --- | --- |
| Language and environment | Python 3.12 and uv; dependencies are declared in pyproject.toml and locked in uv.lock |
| Layout and build | src package layout and Hatchling; installation registers the website-healthcheck command |
| Configuration | YAML, PyYAML, and Pydantic v2; load and validate types, paths, and numeric ranges |
| HTTP | A synchronous HTTPX client; check targets sequentially and reuse connections |
| Database | Python's standard-library sqlite3; a local file, without a separate database server or ORM |
| Scheduling and processes | Linux cron, POSIX shell, fcntl.flock, and psutil; control startup and termination of old instances |
| Alerts | Standard-library smtplib, ssl, and email; Gmail SMTP_SSL, port 465 by default |
| Tests and automation | pytest and GitHub Actions; offline tests, local HTTP integration, and real subprocess tests |

The initial implementation uses no message queue. Development takes place on Mac, with Linux as the deployment target.

## Directory layout and module responsibilities

```text
.
├── README.md                    # English project overview and usage
├── README_CN.md                 # Chinese project overview and usage
├── AGENTS.md                     # Collaboration rules
├── TODO.md                       # Task progress and acceptance records
├── pyproject.toml / uv.lock      # Package configuration, dependencies, and lockfile
├── refs/requirement.md           # Historical exercise requirements
├── src/website_healthcheck/
│   ├── main.py                  # Command entry point coordinating the modules below
│   ├── config/
│   │   ├── load_config.py       # Configuration models, loading, validation, and path resolution
│   │   ├── config.yaml.example  # Credential-free example committed to Git
│   │   ├── config.yaml          # Local real configuration, ignored by Git
│   │   └── var/                 # Default database/lock directory; only .gitkeep is tracked
│   ├── checker/checker.py       # HTTP requests, timing, checks, and retries
│   ├── scheduler/process_lock.py # Verify/stop old processes and acquire the execution lock
│   ├── storage/
│   │   ├── schema.py           # Seven table definitions and field explanations
│   │   ├── models.py           # Events, stored checks, and report data structures
│   │   └── storage.py          # Records, missed/interrupted rounds, and notification state
│   └── notifier/notifier.py    # Build and send summaries; no direct database updates
├── scripts/
│   ├── run_healthcheck.sh      # cron launcher setting the directory and environment
│   ├── print_cron.py           # Print cron entries from configuration; no installation
│   └── verify_release.py       # Check the tag, package version, and main history before release
├── tests/                     # pytest unit and integration tests
├── docs/                      # Detailed module, deployment, test, and design documents
└── .github/workflows/         # ci.yml runs pytest; release.yml verifies and publishes versions
```

| Module | Main interfaces | Responsibility boundary |
| --- | --- | --- |
| config | load_config | Convert YAML into validated configuration without creating runtime data |
| checker | check_page, run_checks | Return final results and all attempts; no database writes or emails |
| scheduler | process_lock | Allow only the instance holding the execution lock into the business flow |
| storage | start_run, finish_run | Persist the start record and complete check results, respectively |
| storage | reconcile_schedule | Identify old interruptions and slots that never started; no replay |
| storage | pending_report, record_delivery | Read a pending-report snapshot and record delivery attempts/success markers |
| notifier | send_alert | Build and send an email; no interval decisions or database writes |
| main | main, _run_once | Coordinate calls, hold the lock, and return an exit code |

Start reading the code at [main.py](src/website_healthcheck/main.py), then follow the modules in the table. See the [implementation design](docs/planning/implementation-plan.md) for detailed interfaces and decisions.

## How one round runs

1. **Load configuration.** `main()` parses `--config` and calls `load_config()`. Invalid configuration returns 2 before checks begin.
2. **Determine the current slot and handle the previous instance.** `_run_once()` calculates the UTC slot from the check interval and calls `process_lock()`. If an older slot's instance is still running, verify its PID, creation time, and open lock file, then request termination and force termination if needed. Continue only after acquiring the lock. An unverified identity or failed termination returns 2; duplicate starts in the same slot and other protective cases return 3.
3. **Check what happened previously.** Open `Storage`, recalculate the current slot using the actual time after acquiring the lock, and call `reconcile_schedule()`. Record unfinished old rounds as interrupted and identify historical missed slots without rerunning them.
4. **Persist the start.** `start_run()` commits a `running` record before any requests and registers the instance's run ID. A crash during HTTP requests therefore leaves a record. If this slot already has a record, do not check it again.
5. **Check current targets and save results.** `run_checks()` checks every target sequentially. In one database transaction, `finish_run()` stores results, attempts, and final-failure events, then changes the run to `completed`.
6. **Handle the summary email.** `pending_report()` checks for unnotified incidents and the minimum mail interval. Call `send_alert()` only when a report exists, then use `record_delivery()` to record the outcome. Success marks this batch notified; failure keeps it pending and returns execution-error code 2.
7. **Finish the round.** Close the database, record that the instance finished, and release the lock. Return 0 if the current targets are healthy, or 1 for a final website failure. cron starts the next round; the process does not wait for it.

For example, if instance A from 12:00 is still running at 12:05, instance B first verifies and stops A, records its interruption, and executes B's current round. B neither resumes nor replays A. If A already saved its check results but became stuck while sending mail, preserve those results and record the interruption as well.

Database states mean:

| State | Meaning |
| --- | --- |
| running | A start record exists but completed check results have not been saved; this value alone does not prove that the process is alive |
| completed | Page check results have been saved; a website may still have failed, and the later email stage may also fail |
| interrupted | After confirming that the previous instance stopped, it was found not to have fully finished; do not resume that round |

The lock covers checks, database operations, and the email stage. Check frequency and minimum mail interval are configured separately. See the [scheduling guide](docs/scheduling.md) for process protection and detailed control flow.

## Installation and first run

Install Git and uv, and ensure Python and the locked dependencies can be downloaded. Obtain the repository and run:

```sh
git clone https://github.com/ACienZ/website-healthcheck.git
cd website-healthcheck
uv python install
uv sync --locked
```

`uv python install` reads `.python-version` to prepare Python 3.12. `uv sync --locked` installs the project and test dependencies without updating the lockfile. If you already have a checkout, start with `uv sync --locked` instead of cloning inside it.

### Explore through tests

After installation, run:

```sh
uv run --locked pytest
uv run --locked website-healthcheck --help
```

This entry point needs no real configuration or Gmail credentials. Tests use temporary databases, HTTP / SMTP substitutes, local loopback HTTP servers, and subprocesses; they do not request the real target websites or send email. Installing dependencies requires download access. See [tests and CI](#tests-and-continuous-integration) for coverage.

### Run with real services

Copy the example only if a real configuration file does not already exist:

```sh
test -e src/website_healthcheck/config/config.yaml || cp src/website_healthcheck/config/config.yaml.example src/website_healthcheck/config/config.yaml
chmod 600 src/website_healthcheck/config/config.yaml
mkdir -p src/website_healthcheck/config/var
chmod 700 src/website_healthcheck/config/var
```

Edit `config.yaml` with the Gmail account, app password, sender, and recipients. The default server is `smtp.gmail.com:465`; the example password is only a placeholder. Review the target URLs and check rules before running.

```sh
uv run --locked website-healthcheck --help
uv run --locked website-healthcheck --config "$PWD/src/website_healthcheck/config/config.yaml"
echo "Exit code: $?"
```

The second command makes real website requests, writes the database, and sends mail when needed. Without `--config`, the command uses `config/config.yaml` inside the installed package; with the current uv editable installation, this is the source configuration. The entry point is the registered `website-healthcheck` command, not a root-level `main.py`.

| Exit code | Meaning |
| --- | --- |
| 0 | Current targets are ultimately healthy, with no execution error |
| 1 | At least one target still fails after retries |
| 2 | Configuration, database, mail, or old-instance termination error; takes precedence over website failure |
| 3 | Duplicate invocation in the same slot, another instance handling takeover, or protection against terminating the current/ancestor process |

A second invocation within the same five-minute slot may return 3. Historical missed slots are recorded and reported; they do not by themselves make currently healthy checks return 1.

## Migration from the previous name

Version `0.2.0` changes the distribution and command from `canlah-healthcheck` to `website-healthcheck`, and Python imports from `canlah_healthcheck` to `website_healthcheck`. The GitHub repository is now [ACienZ/website-healthcheck](https://github.com/ACienZ/website-healthcheck). Older release snapshots retain their original names.

Before updating an existing deployment, pause this project's old cron entry and confirm its running instance has stopped. Preserve the real `config.yaml` and the entire `var/` directory from `src/canlah_healthcheck/config/`, then move them into `src/website_healthcheck/config/` without overwriting existing destination data. Keep the relative storage paths unchanged, or update absolute paths in your own configuration. No database schema conversion is needed.

Run `uv sync --locked` to replace the installed package and command. Update any imports, launcher/config/log paths, and cron entries to the new names; regenerate the cron snippet with `scripts/print_cron.py` and review it before resuming the schedule. The project does not edit crontab or migrate deployed credentials automatically. Existing release tags remain unchanged.

## Configuration

Runtime parameters are managed in the real YAML corresponding to [config.yaml.example](src/website_healthcheck/config/config.yaml.example). Models reject unknown fields and invalid types. Error messages do not expose passwords or complete original inputs.

| Section | Main settings |
| --- | --- |
| targets | A nonempty list of distinct HTTP(S) target URLs |
| check | Expected status, keyword, full-response threshold, request waiting timeout, retry count, and delay |
| schedule | Check interval, missed-execution grace period, and old-instance termination wait |
| storage | SQLite database and execution-lock paths |
| mail | Minimum mail interval, SMTP connection settings, credentials, sender, and recipients |

Both check and mail intervals default to five minutes. `schedule.interval_minutes` must be a positive divisor of 60. `grace_seconds` defaults to 60 seconds and only governs historical missed-execution detection, not HTTP timeouts. `stop_timeout_seconds` defaults to 5 seconds, with up to that wait after requesting termination and again after forcing termination.

Relative database and lock paths are resolved against the configuration file's directory. Runtime data defaults to `config/var/`; all instances using the same database must use the same execution lock. Real `config.yaml` and runtime data are not committed; Git tracks the example and directory placeholder. See the [configuration guide](docs/configuration.md) and [configuration design table](docs/planning/implementation-plan.md#2-配置与目录约定) for all fields, defaults, and validation rules.

## Deployment with a five-minute schedule

On Linux, prepare the configuration at the deployment path, run `uv sync --locked`, and manually run the launcher once. Replace these template paths with real absolute paths:

```sh
/bin/sh /srv/website-healthcheck/scripts/run_healthcheck.sh \
  /absolute/uv \
  /srv/website-healthcheck/src/website_healthcheck/config/config.yaml
```

The launcher changes to the repository directory, sets UTC and UTF-8, and executes the installed command through `uv run --locked --no-sync`. It does not reinstall dependencies on every invocation.

Create a writable log directory and file first, then generate the cron snippet from the repository root:

```sh
uv run --locked python scripts/print_cron.py \
  --uv /absolute/uv \
  --config /srv/website-healthcheck/src/website_healthcheck/config/config.yaml \
  --log /absolute/healthcheck.log
```

The default output has this structure:

```cron
CRON_TZ=UTC
*/5 * * * * /bin/sh /srv/website-healthcheck/scripts/run_healthcheck.sh /absolute/uv /srv/website-healthcheck/src/website_healthcheck/config/config.yaml >> /absolute/healthcheck.log 2>&1
```

Inspect existing entries with `crontab -l`, then append the generated content using `crontab -e`, preserving other tasks. `*/5` triggers at minutes 00, 05, 10, and so on each hour. The log's parent directory must already exist. Do not overwrite the entire crontab with the generator output.

Cronie supports the `CRON_TZ` line above. For other implementations, confirm scheduler timezones using the [deployment guide](docs/deployment.md). Do not assume Mac cron supports this variable; that guide also covers Desktop permissions, sleep, and local testing. Update cron whenever the check interval changes.

Emails are handled only after all current checks finish; no separate mail cron job is installed. The first pending incident can notify immediately after checks finish. Later attempts must be at least `mail.interval_minutes` apart, and actual sending still waits for a completed check round. SMTP failure keeps incidents pending; success marks them notified. No pending incidents means no empty email.

After testing, inspect the entries and open the editor:

```sh
crontab -l
EDITOR=nano crontab -e
```

Delete only the complete task line that calls this project's `scripts/run_healthcheck.sh`, preserving other projects' entries. In Nano, press Ctrl+O and Enter to save, then Ctrl+X to exit. Run `crontab -l` again to confirm that this project's entry is gone; if no entries remain, a message saying there is no crontab is also normal. Do not run `crontab -r`, which deletes all tasks for the current user.

If you added `CRON_TZ=UTC`, remove it only after confirming that other tasks do not depend on it; it may affect the timezone of subsequent entries. Removing a cron entry prevents future starts, while an already running instance continues to completion. Disabling the schedule does not delete configuration, database records, or logs.

## Tests and continuous integration

```sh
uv run --locked pytest
uv run --locked pytest tests/test_http_integration.py -v
```

Tests isolate data using pytest fixtures, temporary directories, and SQLite databases. Unit tests substitute HTTP and SMTP. Local HTTP integration tests establish real loopback connections to verify redirects, full-body timing, timeouts, and retries. Subprocess tests exercise real locks, termination of previous instances, and the installed command.

Local validation for the `0.2.0` rename on 2026-10-29 passed all 272 tests, including a new subprocess check of the installed command's help entry point. The previous version recorded 271 passing tests on 2026-10-18. GitHub Actions runs `uv sync --locked` and `uv run --locked pytest` on Ubuntu/Python 3.12 for pushes and pull requests; verify the actual result for the relevant commit in Actions or PR checks. Tests neither depend on the real Canlah websites or Gmail nor read the user's real configuration. See [test documentation](docs/testing.md) for scope and evidence.

## Releases

Version `0.2.0` is being prepared for the complete rename, following the merged showcase documentation changes. Its tag and Release will be created after the rename PR is reviewed and merged; see [Issue #26](https://github.com/ACienZ/website-healthcheck/issues/26). It is not yet published.

[v0.1.1](https://github.com/ACienZ/website-healthcheck/releases/tag/v0.1.1) was published on 2026-10-18 and includes the MIT license in its source archive. The existing `v0.1.0` snapshot does not contain the license file. Future releases follow the same workflow: after a version PR is merged and publishing is authorized, push its matching tag. Actions verifies that the tagged commit is already in `main`, installs locked dependencies, and runs the complete pytest suite. Only after these steps pass does a separate job create a GitHub Release with generated release notes.

Reruns preserve an existing Release. Version mismatches, unmerged commits, failed tests, or failed release queries prevent creation. Releases use GitHub's source ZIP/tar.gz archives; nothing is published to PyPI and no extra wheel is uploaded. See the [release guide](docs/releases.md) for commands, troubleshooting, and authorization before the first release.

## Data maintenance and troubleshooting

SQLite contains seven tables: `runs` records round states; `checks` stores final page results; `attempts` preserves individual attempts; `events` stores incidents to report; `schedule_state` tracks how far past slots have been checked; and `deliveries` plus `delivery_events` record notification attempts and their included events. Actual retries equal the number of attempts for a check minus one. See the [SQLite guide](docs/storage.md) for fields and relationships.

Recorded times use UTC. cron appends output to the configured log. See [deployment maintenance](docs/deployment.md#查看记录备份与故障排查) for read-only queries, backups, updates, and troubleshooting. Before backing up or replacing the database or updating dependencies, pause this project's cron entry and confirm the active instance has stopped. The initial implementation does not automatically rotate logs, remove history, or migrate schemas.

If no email arrives, first check whether final failures, missed slots, or interruptions exist, then inspect the mail interval and `deliveries`. A target that fails only during retries but ultimately recovers does not create a final-failure email. A lock file remaining on disk does not mean its process is alive; do not delete it to bypass process checks.

## Current limitations and production preparation

- The local program cannot send alerts while its host is down; missed slots are detected after execution resumes. Production needs an independent heartbeat to detect host or cron outages.
- SMTP acceptance does not guarantee inbox delivery. A crash after SMTP acceptance but before database marking, or partial recipient rejection, can cause duplicate notifications.
- The initial implementation is limited to one machine and a local filesystem. It does not coordinate multiple machines or enforce a strict total runtime deadline.
- Before production, add credential management, alert suppression/escalation, log rotation, database retention, backup/restore drills, and validation of real websites, Gmail, cron, and deployment-host permissions.

These production preparations are future recommendations; the initial implementation does not automate them. They mean:

| Item | Practical action | Problem addressed |
| --- | --- | --- |
| Independent heartbeat | Report whether each round finishes on time to another machine or external monitor; let that monitor alert when reports stop arriving | A stopped host or cron cannot send its own outage alert |
| Credential management | Store the SMTP password in deployment-managed secret storage with restricted access and rotation; adapt configuration loading accordingly | The current password remains in local YAML; ignoring it in Git does not prevent exposure on the host |
| Alert suppression | Define repeat-notification intervals, group related incidents, and silence alerts during maintenance | Reduce repeated mail about one incident; the current minimum mail interval only limits frequency |
| Alert escalation | Define when a persistent or severe incident reaches a backup owner or another channel | Ensure follow-up if the first recipient misses or does not resolve it; this is not process privilege escalation |
| Log rotation | Switch logs by date or size, compress older files, and limit retained files | Prevent an indefinitely appended cron.log from filling the disk |
| Data retention | Define how long checks, attempts, events, and delivery history are kept; archive or clean up with table relationships in mind, preserving unnotified incidents | The initial SQLite database only grows; disk use and the investigation window need explicit policies |
| Backup and restore drills | Back up SQLite regularly, restore a copy to an isolated directory, check integrity, and verify that it can be queried and used | The existence of a backup file alone does not prove recovery will work after corruption or deletion |
| Real deployment validation | On the target host, check website DNS/TLS/content, Gmail authentication and actual receipt, cron triggers and file permissions, and detection after downtime | Local HTTP and SMTP substitutes cannot prove that the real deployment works |
