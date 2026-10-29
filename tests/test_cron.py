"""验证 cron 生成与真实 /bin/sh 启动；不安装 crontab、不请求网站或 SMTP。

脚本放在 scripts/，用 importlib 按文件加载纯生成函数；测试 subprocess 时
显式传入参数列表。只有验证“生成后的 shell 命令”时才使用 /bin/sh -c。
"""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from website_healthcheck.config.load_config import load_config
from website_healthcheck.scheduler.process_lock import process_lock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/print_cron.py"
LAUNCHER = ROOT / "scripts/run_healthcheck.sh"
EXAMPLE = ROOT / "src/website_healthcheck/config/config.yaml.example"
spec = importlib.util.spec_from_file_location("print_cron", SCRIPT)
cron = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cron)


@pytest.fixture
def paths(tmp_path):
    """路径特意包含空格、中文、引号和 shell 替换语法，验证它们只作为数据。"""
    directory = tmp_path / "中文 repo ' space"
    directory.mkdir()
    config = directory / "config ' $(touch SHOULD_NOT_EXIST).yaml"
    config.write_text(EXAMPLE.read_text())
    uv = directory / "fake uv"
    # 假 uv 不读取配置，只将收到的参数打印出来；避免真的安装或联网。
    uv.write_text('''#!/bin/sh
printf 'cwd=%s\n' "$PWD"
printf 'tz=%s\n' "$TZ"
printf 'arg=%s\n' "$@"
exit "${CANLAH_TEST_EXIT:-0}"
''')
    uv.chmod(0o700)
    return config, uv, directory / "healthcheck log.txt"


@pytest.mark.parametrize("interval", [1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60])
def test_every_supported_interval(interval, paths):
    """覆盖配置接受的所有周期；特别检查每小时不能生成 */60。"""
    config, uv, log = paths
    text = cron.cron_text(interval, uv, config, log)
    minute = "0" if interval == 60 else f"*/{interval}"
    assert text.startswith(f"CRON_TZ=UTC\n{minute} * * * * ")
    assert text.endswith(" 2>&1\n")


def test_generated_command_quotes_paths_and_captures_log(paths, tmp_path):
    """真正执行生成的命令，验证路径引用、日志追加和工作目录，无 shell 注入。"""
    config, uv, log = paths
    log.write_text("existing log\n")
    command = cron.cron_text(5, uv, config, log).splitlines()[1].split(" ", 5)[5]
    result = subprocess.run(
        ["/bin/sh", "-c", command], cwd=tmp_path,
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0
    text = log.read_text()
    assert text.startswith("existing log\n")
    assert f"cwd={ROOT}\n" in text
    assert "tz=UTC\n" in text
    assert "arg=run\narg=--locked\narg=--no-sync\narg=website-healthcheck\narg=--config\n" in text
    assert f"arg={config}\n" in text
    assert not (ROOT / "SHOULD_NOT_EXIST").exists()
    assert result.stdout == result.stderr == ""


@pytest.mark.parametrize("status", [0, 1, 2, 3])
def test_launcher_preserves_exit_code(status, paths, tmp_path, monkeypatch):
    """用真实 shell 验证 exec 保留退出码；fake uv 不替代被测 shell 行为。"""
    config, uv, log = paths
    monkeypatch.setenv("CANLAH_TEST_EXIT", str(status))
    # 将启动器复制到带空格的仓库路径，也验证按脚本位置定位仓库。
    copied = tmp_path / "copied repo ' 中文" / "scripts" / LAUNCHER.name
    copied.parent.mkdir(parents=True)
    shutil.copyfile(LAUNCHER, copied)
    result = subprocess.run(
        ["/bin/sh", str(copied), str(uv), str(config)], cwd=tmp_path,
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == status
    assert f"cwd={copied.parent.parent}\n" in result.stdout
    assert f"arg={config}\n" in result.stdout


@pytest.mark.parametrize("arguments", [[], ["uv", "/tmp/config.yaml"], ["/uv", "config.yaml"], ["/missing-uv", "/tmp/config.yaml"]])
def test_launcher_rejects_invalid_paths(arguments, tmp_path):
    """参数数量、相对路径和不可执行 uv 都应在运行 Python 前失败。"""
    result = subprocess.run(
        ["/bin/sh", str(LAUNCHER), *arguments], cwd=tmp_path,
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 2
    assert result.stdout == ""


@pytest.mark.parametrize("character", ["%", "\n", "\r", "\x00"])
def test_reject_cron_control_characters(character, paths):
    """cron 对百分号另有解释，shell 单引号也不能代替这一层检查。"""
    config, uv, log = paths
    with pytest.raises(ValueError, match="cron 路径"):
        cron.cron_text(5, uv, config, log.with_name("bad" + character + ".log"))


def test_helper_reads_interval_without_side_effects(paths, capsys):
    """读取 YAML 的真实周期，不写配置、创建 var 或连接服务。"""
    config, uv, log = paths
    data = yaml.safe_load(config.read_text())
    data["schedule"]["interval_minutes"] = 60
    config.write_text(yaml.safe_dump(data))
    before = config.read_text()
    assert cron.main(["--config", str(config), "--uv", str(uv), "--log", str(log)]) == 0
    output = capsys.readouterr()
    assert "\n0 * * * * " in output.out
    assert output.err == ""
    assert not (config.parent / "var").exists()
    assert not log.exists()
    assert config.read_text() == before


@pytest.mark.parametrize("log_name", ["config", "database", "lock", "startup", "owner", "owner_tmp"])
def test_helper_rejects_log_over_runtime_files(log_name, paths, capsys):
    """不允许把日志追加到配置或运行数据上。"""
    config, uv, log = paths
    destinations = {
        "config": config, "database": config.parent / "var/healthcheck.sqlite3",
        "lock": config.parent / "var/healthcheck.lock",
        "startup": config.parent / "var/healthcheck.lock.startup",
        "owner": config.parent / "var/healthcheck.lock.owner.json",
        "owner_tmp": config.parent / "var/healthcheck.lock.owner.json.tmp",
    }
    assert cron.main(["--config", str(config), "--uv", str(uv), "--log", str(destinations[log_name])]) == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("invalid", ["missing_config", "bad_interval", "missing_uv", "percent_path"])
def test_helper_error_is_safe(invalid, paths, capsys):
    """无效配置或路径返回 2，不打印完整 YAML 或占位密码。"""
    config, uv, log = paths
    if invalid == "missing_config":
        config = config.with_name("missing.yaml")
    elif invalid == "bad_interval":
        data = yaml.safe_load(config.read_text())
        data["schedule"]["interval_minutes"] = 7
        config.write_text(yaml.safe_dump(data))
    elif invalid == "missing_uv":
        uv = uv.with_name("missing uv")
    else:
        log = log.with_name("bad%.log")
    assert cron.main(["--config", str(config), "--uv", str(uv), "--log", str(log)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "REPLACE_WITH_GMAIL_APP_PASSWORD" not in output.err


def test_helper_subprocess_from_other_directory(paths, tmp_path):
    """使用已安装包运行辅助脚本，不要求当前目录是仓库。"""
    config, uv, log = paths
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config), "--uv", str(uv), "--log", str(log)],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("CRON_TZ=UTC\n*/5 ")
    assert result.stderr == ""


def test_launcher_with_real_uv_and_busy_lock(paths, tmp_path):
    """真实 uv → 安装入口 → 父辈保护；持锁的是测试父进程，不得终止或发外部请求。"""
    config_path, fake_uv, log = paths
    config = load_config(config_path)
    uv = shutil.which("uv")
    assert uv is not None, "项目测试环境须具备 uv"
    with process_lock(config.storage.lock_path):
        result = subprocess.run(
            ["/bin/sh", str(LAUNCHER), str(Path(uv).resolve()), str(config_path)],
            cwd=tmp_path, capture_output=True, text=True, check=False, timeout=10,
        )
    assert result.returncode == 3
    assert "已有任务运行" in result.stderr
    assert not config.storage.database_path.exists()
