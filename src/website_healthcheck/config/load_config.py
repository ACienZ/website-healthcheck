"""读取 YAML 配置，校验字段并隐藏错误中的原始输入。

阅读顺序建议：先看最下方 load_config，再看 Config，最后看各 Settings 类。
与 GmailAutomate 的原写法一样，主流程仍是：
    YAML 文件 → yaml.safe_load 得到字典 → Pydantic 模型得到配置对象。
原来的 Config(**config_data) 与这里 Config.model_validate(data) 都会触发校验；
这里增加的是字段范围、跨字段约束、相对路径解析和错误脱敏。
这些类只描述配置，不执行 HTTP 请求、调度、数据库连接或邮件发送。
"""

from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import (
    BaseModel, ConfigDict, Field, HttpUrl, SecretStr, ValidationError,
    field_validator, model_validator,
)

# Annotated[原类型, 附加规则]：为类型附上 Pydantic 能识别的校验信息。
# 它们是可复用的类型别名，不是运行时创建出来的配置对象。
# min_length=1 拒绝空字符串；正则 \S 要求至少有一个非空白字符。
NonEmpty = Annotated[str, Field(min_length=1, pattern=r"\S")]
# gt 表示 >，ge 表示 >=；禁止 NaN/Infinity，避免时间比较得到异常结果。
PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
NonNegativeSeconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]
# 仅检查常见地址结构，不进行 DNS 查询或引入额外邮件校验依赖。
MailAddress = Annotated[str, Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")]


class ConfigError(ValueError):
    """可向用户展示的脱敏配置错误。"""


class Settings(BaseModel):
    """所有配置段共用严格类型检查，并拒绝未知字段。"""

    # 子类继承这套规则，不必为每个配置段重复写一遍。
    # extra="forbid"：例如把 retries 拼成 retry，会报错而不是悄悄忽略。
    # strict=True：不把字符串 "2" 或布尔值 True 自动当作整数配置。
    # validate_default=True：默认值也要经过字段规则和自定义校验器。
    # hide_input_in_errors=True：Pydantic 异常的文本表示不展示原始输入；
    # 这不是加密，也不会自动清除 errors() 返回的输入，加载函数还要单独脱敏。
    model_config = ConfigDict(
        extra="forbid", strict=True, validate_default=True, hide_input_in_errors=True,
    )


class CheckSettings(Settings):
    """页面检查阈值与重试参数。"""

    # “字段名: 类型 = 默认值”既声明属性类型，也说明 YAML 省略此项时的值。
    # Field 可同时声明默认值与取值范围；ge/le 分别表示 >= 和 <=。
    expected_status: int = Field(default=200, ge=100, le=599)
    keyword: NonEmpty = "Canlah"
    max_response_seconds: PositiveSeconds = 5.0
    request_timeout_seconds: PositiveSeconds = 10.0
    retries: int = Field(default=2, ge=0)
    retry_delay_seconds: NonNegativeSeconds = 2.0


class ScheduleSettings(Settings):
    """调度周期及漏跑判定宽限，不控制 HTTP 超时。"""

    interval_minutes: int = Field(default=5, gt=0, le=60)
    # 预期启动时间过去 60 秒后，才将未启动的任务判为漏跑。
    grace_seconds: NonNegativeSeconds = 60.0
    # 新时段启动时，先请求旧实例退出，最多等 5 秒；仍未退出则强制结束，再等 5 秒。
    # 这个值只控制停止旧进程的等待，与“没启动多久算漏跑”的 grace_seconds 不同。
    stop_timeout_seconds: PositiveSeconds = 5.0

    # field_validator 只负责一个字段。默认在 Pydantic 完成类型/范围校验后执行，
    # 因而进入函数时 value 已经是 1~60 的整数，不会出现对零取模。
    # classmethod 的 cls 是模型类；此时尚未构造完整配置对象。
    @field_validator("interval_minutes")
    @classmethod
    def validate_interval(cls, value: int) -> int:
        # cron 的 */N 是每小时从第 0 分钟重新计数，不是任意 N 分钟的连续间隔。
        # 只接受 60 的因数，避免 */7 在跨小时处产生不均匀间隔。
        if 60 % value:
            raise ValueError("检查周期必须是 60 的正整数因数")
        return value

    # model_validator 校验整个对象，适合比较两个字段。
    # mode="after" 表示字段已经解析完成，可通过 self 访问；Self 表示返回同类对象。
    @model_validator(mode="after")
    def validate_grace(self) -> Self:
        if self.grace_seconds >= self.interval_minutes * 60:
            raise ValueError("宽限时间必须小于检查周期")
        # after 模型校验器必须返回验证后的对象，后续才能继续使用。
        return self


class StorageSettings(Settings):
    """数据库与进程锁路径，相对于配置文件所在目录。"""

    # YAML 路径是字符串，而 Python 用 Path 操作路径。
    # 这里局部 strict=False，允许字符串转 Path；其他配置仍保持严格校验。
    database_path: Path = Field(default=Path("var/healthcheck.sqlite3"), strict=False)
    lock_path: Path = Field(default=Path("var/healthcheck.lock"), strict=False)

    # before 先检查原始字符串，再交给 Pydantic 转换为 Path。
    # 若等转换后才检查，Path("") 已经变成当前目录 "."，就无法辨认空配置了。
    # 一个校验器可以同时用于两个遵守相同规则的字段。
    @field_validator("database_path", "lock_path", mode="before")
    @classmethod
    def validate_path(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("路径不能为空")
        return value


class MailSettings(Settings):
    """SMTP 连接与汇总参数；密码使用 SecretStr 隐藏默认输出。"""

    interval_minutes: int = Field(default=5, gt=0)
    host: NonEmpty = "smtp.gmail.com"
    port: int = Field(default=465, ge=1, le=65535)
    # 没有“= 默认值”的字段必须由 YAML 提供，缺失时报告 missing。
    username: NonEmpty
    # SecretStr 在 repr/JSON 中默认显示星号，但内存中仍保存原密码，不是加密。
    # 只有将来真正登录 SMTP 时，才应调用 get_secret_value() 取出明文。
    password: SecretStr = Field(min_length=1)
    sender: MailAddress
    # list 的 min_length 约束收件人数量；MailAddress 约束列表中的每个元素。
    recipients: list[MailAddress] = Field(min_length=1)
    timeout_seconds: PositiveSeconds = 10.0

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        # min_length 只能排除空字符串，此处进一步排除全是空格的密码。
        if not value.get_secret_value().strip():
            raise ValueError("密码不能为空")
        return value


class Config(Settings):
    """配置根节点，组合检查、调度、存储与邮件设置。"""

    # HttpUrl 负责验证 HTTP(S) URL，并生成 URL 对象；请求时可用 str(url)。
    targets: list[HttpUrl] = Field(min_length=1)
    # default_factory 是“需要默认值时调用的函数/类”，这里会创建新的子配置对象。
    # 因此省略整个 check 段也能使用 CheckSettings 中的默认参数。
    check: CheckSettings = Field(default_factory=CheckSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    mail: MailSettings

    @field_validator("targets")
    @classmethod
    def validate_targets(cls, value: list[HttpUrl]) -> list[HttpUrl]:
        # 先由 HttpUrl 解析，再去重；例如 https://example.com 与带结尾 / 的写法
        # 会被规范化为相同 URL，避免同一目标被重复检查。
        if len(set(value)) != len(value):
            raise ValueError("目标 URL 不能重复")
        return value


def _safe_location(location: tuple) -> str:
    """只展示已声明字段名和列表下标，不回显未知 YAML 键。"""
    # Pydantic 的 loc 是定位元组，例如 ("mail", "recipients", 0)。
    # 希望输出 mail.recipients.0，但不能直接拼接所有原始键：
    # 用户可能误把密码写成 YAML 键，未知键也可能包含敏感内容。
    model = Config
    sections = {
        "check": CheckSettings, "schedule": ScheduleSettings,
        "storage": StorageSettings, "mail": MailSettings,
    }
    parts = []
    for part in location:
        if isinstance(part, int):
            parts.append(str(part))
        # model_fields 是模型声明的字段集合，相当于可展示字段名的白名单。
        elif part in model.model_fields:
            parts.append(part)
            # 进入 mail/check 等配置段后，下一项应按对应子模型的字段检查。
            model = sections.get(part, model)
        else:
            parts.append("<未知字段>")
    return ".".join(parts) or "配置根节点"


def load_config(path: Path) -> Config:
    """加载配置；存储路径相对于配置目录解析，不创建目录或连接外部服务。"""
    # 1. 统一成 Path。类型注解不会自动转换实参，显式转换也允许调用方传字符串。
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as stream:
            # safe_load 只解析常规 YAML 数据，不构造任意 Python 对象。
            # 此时还只是 dict/list/数字等原始数据，并没有进行业务字段校验。
            data = yaml.safe_load(stream)
    # 2. 将底层读文件/解析错误转换为统一 ConfigError。
    # raise ... from None 隐藏原始异常链的默认 traceback 展示，防止 YAML 原文泄露。
    # 它不是从内存中删除底层异常；调用方也不应主动打印原始异常上下文或局部变量。
    except FileNotFoundError:
        raise ConfigError("配置文件不存在") from None
    except (OSError, UnicodeError):
        raise ConfigError("无法读取配置文件，请检查权限及 UTF-8 编码") from None
    except yaml.YAMLError:
        # YAML 异常可能包含原始行和密码，不直接输出解析器异常。
        raise ConfigError("配置文件 YAML 格式无效") from None

    # 空文件会被 safe_load 解析为 None；合法 YAML 也可能是列表或数字。
    # 我们需要包含 targets、mail 等字段的字典，所以先检查根节点结构。
    if not isinstance(data, dict) or not data:
        raise ConfigError("配置根节点必须是非空映射")
    try:
        # 3. 一次构造完整对象；嵌套 Settings 与所有 validator 都由 Pydantic 调用，
        # 不需要我们手动依次调用 validate_interval、validate_password 等函数。
        config = Config.model_validate(data)
    except ValidationError as exc:
        # errors() 返回错误字典列表。只保留安全位置 loc 与错误类别 type，
        # 例如 mail.password: missing；不输出输入值、异常上下文或原始 msg。
        # include_* 参数控制返回的错误细节，不影响是否执行校验。
        details = "; ".join(
            f"{_safe_location(error['loc'])}: {error['type']}"
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        )
        raise ConfigError(f"配置校验失败：{details}") from None

    # 4. resolve() 转成绝对路径，parent 取配置所在目录，避免依赖启动命令的目录。
    # Path 的 / 表示拼接路径；右侧若已经是绝对路径，则保留该绝对路径。
    # 这里只计算位置，不 mkdir、不创建 SQLite 文件。
    base = path.resolve().parent
    config.storage.database_path = (base / config.storage.database_path).resolve()
    config.storage.lock_path = (base / config.storage.lock_path).resolve()
    # 先规范化路径再比较，防止不同写法实际指向同一文件。
    lock = config.storage.lock_path
    # 停止旧实例还会使用这些辅助文件，不能让进程信息意外覆盖数据库。
    lock_files = {lock, *(lock.with_name(lock.name + suffix) for suffix in (
        ".startup", ".owner.json", ".owner.json.tmp",
    ))}
    if config.storage.database_path in lock_files:
        raise ConfigError("storage：数据库与锁及进程信息文件不能使用同一路径")
    return config
