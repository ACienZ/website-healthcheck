"""配置边界、路径解析及错误脱敏测试；不连接真实服务。

运行全部测试：uv run --locked pytest
仅运行本文件：uv run --locked pytest tests/test_load_config.py -v
仅看某项测试：uv run --locked pytest tests/test_load_config.py -k defaults -v

pytest 会自动收集 test_ 开头的函数，不需要 main()，也不必继承测试基类。
每项测试通常分三步：准备输入（Arrange）→ 调用代码（Act）→ assert 验证（Assert）。
assert 条件为假即测试失败；pytest 会展示实际值，帮助定位问题。
本文件只验证配置，不测试真实 HTTP、数据库写入或 SMTP 登录。
"""

from pathlib import Path
import traceback

import pytest
import yaml

from website_healthcheck.config.load_config import ConfigError, load_config

# __file__ 是本测试文件的位置；parents[1] 是项目根目录，避免依赖运行时 cwd。
CONFIG_DIR = Path(__file__).resolve().parents[1] / "src/website_healthcheck/config"
EXAMPLE = CONFIG_DIR / "config.yaml.example"
# 人工构造的测试字符串，不是真实凭证；用于检查明文是否意外出现在输出中。
SECRET = "test-only-SMTP-secret"


# fixture 是 pytest 管理的测试准备函数。测试函数声明参数 data 时，
# pytest 会调用这个同名 fixture，将返回值注入参数，不需要测试自己调用 data()。
# 默认 scope="function"：每个测试用例都会得到新字典，修改不会污染下一项测试。
@pytest.fixture
def data():
    """每项测试重新读取无凭证示例，避免修改真实配置。"""
    config = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    config["mail"]["password"] = SECRET
    return config


# tmp_path 是 pytest 内置 fixture，为每项测试提供独立临时目录（Path 对象）。
# fixture 也能依赖其他 fixture，因此这里声明 tmp_path 即可使用。
@pytest.fixture
def save(tmp_path):
    """将测试配置写入隔离目录，不污染源码中的 config/var。"""
    # 返回函数而非文件：测试可以先修改 data，再用 save(data) 写出配置。
    # write 通过闭包保留本次测试的 tmp_path；safe_dump 将字典序列化为 YAML。
    def write(data):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return path
    return write


def test_example_loads():
    """项目提供的示例本身必须能通过校验，并解析到约定的数据目录。"""
    config = load_config(EXAMPLE)
    assert [str(url) for url in config.targets] == [
        "https://canlah.ai/", "https://canlah.ai/playbook/",
    ]
    # 示例迁入 config 后，数据目录仍以示例所在位置解析。
    assert config.storage.database_path == CONFIG_DIR / "var/healthcheck.sqlite3"
    assert config.storage.lock_path == CONFIG_DIR / "var/healthcheck.lock"
    assert config.check.retries == 2
    assert config.schedule.interval_minutes == config.mail.interval_minutes == 5


def test_defaults(data, save):
    """删除可选段，验证默认配置由模型补齐，而不是从示例残留数据中读到。"""
    # data/save 是注入的字典和写文件函数；与参数化测试中的普通输入不同。
    for section in ("check", "schedule", "storage"):
        del data[section]
    config = load_config(save(data))
    assert config.check.max_response_seconds == 5
    assert config.check.request_timeout_seconds == 10
    assert config.schedule.grace_seconds == 60
    assert config.schedule.stop_timeout_seconds == 5
    assert config.storage.database_path.name == "healthcheck.sqlite3"


def test_paths_are_relative_to_config_not_cwd(data, save, tmp_path, monkeypatch):
    """模拟从另一个工作目录启动，确认相对路径仍以配置所在目录为准。"""
    # monkeypatch 是内置 fixture：临时修改环境/属性/工作目录，并在测试后自动恢复。
    path = save(data)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    config = load_config(path)
    assert config.storage.database_path == tmp_path / "var/healthcheck.sqlite3"
    assert config.storage.lock_path == tmp_path / "var/healthcheck.lock"
    # 加载配置只计算路径，不应顺手创建运行目录或数据库。
    assert not (tmp_path / "var").exists()


def test_absolute_path_preserved(data, save, tmp_path):
    """绝对路径不能再被拼接到配置目录下面。"""
    absolute = tmp_path / "database.sqlite3"
    data["storage"]["database_path"] = str(absolute)
    assert load_config(save(data)).storage.database_path == absolute


# parametrize 将下面的每一行展开成独立测试，而不是在一个测试里手写 for 循环。
# 三列分别传给 section、key、value；data/save 仍由 fixture 提供。
# 因此某一行失败不会阻止其他行被执行，报告也能指出是哪一组输入出错。
# 每组只破坏一个字段，其他字段保持有效，方便定位具体校验规则。
@pytest.mark.parametrize("section,key,value", [
    (None, "targets", []),
    (None, "targets", ["ftp://example.com"]),
    (None, "targets", ["https://example.com", "https://example.com/"]),
    (None, "unknown", "value"),
    ("check", "unknown", "value"),
    ("check", "expected_status", 600),
    ("check", "expected_status", 99),
    ("check", "keyword", " "),
    ("check", "max_response_seconds", 0),
    ("check", "max_response_seconds", float("nan")),
    ("check", "request_timeout_seconds", float("inf")),
    ("check", "request_timeout_seconds", -1),
    ("check", "retries", -1),
    ("check", "retries", True),
    ("check", "retries", "2"),
    ("check", "retry_delay_seconds", -1),
    ("schedule", "interval_minutes", 7),
    ("schedule", "interval_minutes", 0),
    ("schedule", "interval_minutes", 61),
    ("schedule", "grace_seconds", 300),
    ("schedule", "grace_seconds", -1),
    ("schedule", "stop_timeout_seconds", 0),
    ("schedule", "stop_timeout_seconds", -1),
    ("schedule", "stop_timeout_seconds", float("inf")),
    ("schedule", "stop_timeout_seconds", True),
    ("storage", "database_path", ""),
    ("mail", "password", ""),
    ("mail", "password", " "),
    ("mail", "password", 123),
    ("mail", "username", ""),
    ("mail", "sender", "invalid-address"),
    ("mail", "recipients", []),
    ("mail", "recipients", ["invalid-address"]),
    ("mail", "port", 65536),
    ("mail", "interval_minutes", 0),
    ("mail", "timeout_seconds", 0),
])
def test_invalid_values(data, save, section, key, value):
    """每组无效配置都应被统一包装成 ConfigError。"""
    # section=None 表示修改根节点；否则只修改 check/mail 等子字典。
    target = data if section is None else data[section]
    target[key] = value
    # pytest.raises 表示“预期这里抛出异常”：不抛异常或类型错误，测试都会失败。
    # match 是匹配异常消息的正则表达式，不是字符串完全相等比较。
    with pytest.raises(ConfigError, match="配置校验失败"):
        load_config(save(data))


@pytest.mark.parametrize("field", ["username", "password", "sender", "recipients"])
def test_missing_mail_fields(data, save, field):
    """逐个删除必填邮件字段，检查错误能定位到对应字段。"""
    del data["mail"][field]
    with pytest.raises(ConfigError, match=f"mail.{field}: missing"):
        load_config(save(data))


def test_boundary_values_and_independent_mail_interval(data, save):
    """合法边界应通过：可以禁用重试，邮件间隔也不受 cron 周期因数限制。"""
    data["check"].update(retries=0, retry_delay_seconds=0)
    data["schedule"].update(interval_minutes=60, grace_seconds=0)
    data["mail"]["interval_minutes"] = 17
    config = load_config(save(data))
    assert config.check.retries == 0
    assert config.mail.interval_minutes == 17


def test_database_and_lock_must_differ(data, save):
    """数据库和锁文件必须分开，避免两种用途操作同一文件。"""
    data["storage"]["lock_path"] = data["storage"]["database_path"]
    with pytest.raises(ConfigError, match="同一路径"):
        load_config(save(data))


@pytest.mark.parametrize("raw", ["", "null", "[]", "{}", "42", "- item"])
def test_empty_or_non_mapping(tmp_path, raw):
    """YAML 语法合法不等于配置有效：空值、列表、数字都不是所需的非空字典。"""
    path = tmp_path / "config.yaml"
    path.write_text(raw)
    with pytest.raises(ConfigError, match="非空映射"):
        load_config(path)


def test_missing_file(tmp_path):
    """不存在的路径应报告配置缺失，不直接暴露底层 FileNotFoundError。"""
    with pytest.raises(ConfigError, match="不存在"):
        load_config(tmp_path / "absent.yaml")


def test_read_error(tmp_path):
    """把目录当文件读，稳定触发读取失败；不依赖操作系统用户权限来构造案例。"""
    with pytest.raises(ConfigError, match="无法读取"):
        load_config(tmp_path)


def test_invalid_utf8(tmp_path):
    """写入非 UTF-8 字节，确认解码失败也被转换成安全的配置错误。"""
    path = tmp_path / "config.yaml"
    path.write_bytes(b"\xff")
    with pytest.raises(ConfigError, match="UTF-8"):
        load_config(path)


def test_password_repr_and_json_are_masked(data, save):
    """普通展示应隐藏密码，但显式获取明文仍可供将来的 SMTP 登录使用。"""
    # repr 是对象的调试字符串；model_dump_json 是 Pydantic 的 JSON 序列化。
    config = load_config(save(data))
    assert SECRET not in repr(config)
    assert SECRET not in config.model_dump_json()
    assert config.mail.password.get_secret_value() == SECRET


@pytest.mark.parametrize("kind", ["field", "unknown_key", "yaml"])
def test_error_and_traceback_do_not_expose_secret(data, save, kind):
    """检查三条泄露路径：字段值非法、敏感文本误作键、YAML 语法错误。"""
    if kind == "field":
        data["check"]["retries"] = SECRET
    elif kind == "unknown_key":
        data["mail"][SECRET] = SECRET
    path = save(data)
    if kind == "yaml":
        # 故意不闭合括号，迫使 YAML 解析器报错；解析器原文可能包含 SECRET。
        path.write_text(f"mail: [{{password: {SECRET}\n", encoding="utf-8")
    # as caught 获取 pytest 的异常信息对象，caught.value 才是实际异常实例。
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert SECRET not in str(caught.value)
    # 仅检查 str(exception) 不够；还要确认默认 traceback 没有显示底层异常链。
    # format_exception 返回多段字符串，用 join 拼起来再搜索测试密码。
    assert SECRET not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("suffix", [".startup", ".owner.json", ".owner.json.tmp"])
def test_database_cannot_be_a_lock_information_file(data, save, suffix):
    """自定义存储路径也不能让 PID 信息覆盖数据库；只加载配置，不创建文件。"""
    data["storage"]["database_path"] = data["storage"]["lock_path"] + suffix
    with pytest.raises(ConfigError, match="同一路径"):
        load_config(save(data))
