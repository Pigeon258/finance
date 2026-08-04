from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path, PurePosixPath
from typing import Any

import tinycss2
from django.conf import settings
from django.templatetags.static import static

THEME_SCHEMA_VERSION = 1
THEME_CONTRACT_VERSION = 1
SAFE_DEFAULT_THEME_ID = "safe-default"

REGISTERED_PARTS = frozenset(
    {
        "action-group",
        "app-shell",
        "auth-panel",
        "chart-panel",
        "content-panel",
        "data-table",
        "form-panel",
        "message-banner",
        "metric-card",
        "modal-panel",
        "navigation-menu",
        "page-header",
        "status-badge",
        "top-navigation",
    }
)
SUPPORTED_CAPABILITIES = frozenset({"background", "charts", "safe-css", "tokens"})
ALLOWED_FILE_SUFFIXES = frozenset(
    {".css", ".json", ".jpeg", ".jpg", ".png", ".txt", ".webp", ".woff2"}
)
ALLOWED_CSS_PROPERTIES = frozenset(
    {
        "animation",
        "animation-delay",
        "animation-direction",
        "animation-duration",
        "animation-fill-mode",
        "animation-iteration-count",
        "animation-name",
        "animation-play-state",
        "animation-timing-function",
        "background",
        "background-color",
        "background-image",
        "background-position",
        "background-repeat",
        "background-size",
        "border",
        "border-color",
        "border-radius",
        "border-style",
        "border-width",
        "box-shadow",
        "color",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "gap",
        "letter-spacing",
        "line-height",
        "margin",
        "margin-block",
        "margin-inline",
        "max-width",
        "min-height",
        "opacity",
        "padding",
        "padding-block",
        "padding-inline",
        "text-align",
        "text-decoration",
        "text-shadow",
        "text-transform",
        "transform",
        "transform-origin",
        "transition",
        "transition-delay",
        "transition-duration",
        "transition-property",
        "transition-timing-function",
    }
)
ALLOWED_CSS_FUNCTIONS = frozenset(
    {
        "calc",
        "clamp",
        "cubic-bezier",
        "hsl",
        "hsla",
        "linear-gradient",
        "max",
        "min",
        "radial-gradient",
        "rgb",
        "rgba",
        "url",
        "var",
    }
)

_THEME_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_TOKEN_NAME_RE = re.compile(r"^--pf-[a-z0-9-]{1,60}$")
_PART_SELECTOR_RE = re.compile(
    r"^\[data-pf-part=(?:\"(?P<double>[a-z0-9-]+)\"|'(?P<single>[a-z0-9-]+)')\]"
    r"(?P<states>(?:\[data-[a-z-]+=(?:\"[a-z0-9-]+\"|'[a-z0-9-]+')\])*)"
    r"(?:(?::(?:active|checked|disabled|focus|focus-visible|hover))|(?:::(?:after|before)))*$"
)
_STATE_ATTRIBUTE_RE = re.compile(
    r"\[data-(?P<name>[a-z-]+)=(?:\"(?P<double>[a-z0-9-]+)\"|'(?P<single>[a-z0-9-]+)')\]"
)
DOCUMENTED_STATE_VALUES = {
    "appearance-mode": frozenset({"auto", "dark", "light"}),
    "reduce-motion": frozenset({"false", "true"}),
    "status": frozenset({"danger", "info", "neutral", "success", "warning"}),
    "theme-background": frozenset({"false", "true"}),
}


class ThemeValidationError(ValueError):
    """主题包未通过失败关闭校验。"""


@dataclass(frozen=True)
class ThemeDescriptor:
    id: str
    name: str
    version: str
    capabilities: frozenset[str]
    source: str
    root: Path | None
    revision: str
    stylesheet_url: str
    preview_url: str
    config: dict[str, Any]

    @property
    def appearance(self) -> str:
        return self.config.get("appearance", "light")

    @property
    def chart_theme(self) -> dict[str, Any]:
        return self.config.get("charts", {})

    @property
    def cache_key(self) -> str:
        return f"pf-theme:{self.id}:{self.version}:{self.revision}"


@dataclass(frozen=True)
class ThemeSelection:
    theme: ThemeDescriptor
    requested_id: str
    fallback_reason: str


def _application_version() -> str:
    try:
        return package_version("personal-finance")
    except PackageNotFoundError:
        return "0.1.0"


def _semver_triplet(value: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        raise ThemeValidationError(f"版本 {value!r} 不是有效的 SemVer。")
    return tuple(int(part) for part in match.groups()[:3])


def _safe_relative_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ThemeValidationError("主题文件路径无效。")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ThemeValidationError("主题文件路径不得越过主题目录。")
    if path.suffix.lower() not in ALLOWED_FILE_SUFFIXES:
        raise ThemeValidationError(f"主题文件类型不受支持：{path.suffix or '无扩展名'}。")
    return path


def _json_object(path: Path, *, max_bytes: int = 256 * 1024) -> dict[str, Any]:
    if path.stat().st_size > max_bytes:
        raise ThemeValidationError(f"{path.name} 超过允许大小。")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ThemeValidationError(f"{path.name} 不是有效的 UTF-8 JSON。") from error
    if not isinstance(value, dict):
        raise ThemeValidationError(f"{path.name} 顶层必须是对象。")
    return value


def _walk_css_tokens(tokens, *, asset_paths: frozenset[str]) -> None:
    for token in tokens:
        if token.type in {"error", "bad-string", "bad-url"}:
            raise ThemeValidationError("Safe CSS 包含无法可靠解析的值。")
        if token.type == "url":
            _validate_css_url(token.value, asset_paths)
        if token.type == "function":
            function_name = token.lower_name
            if function_name not in ALLOWED_CSS_FUNCTIONS:
                raise ThemeValidationError(f"Safe CSS 函数不在白名单中：{function_name}。")
            if function_name == "url":
                raw_url = tinycss2.serialize(token.arguments).strip().strip("\"'")
                _validate_css_url(raw_url, asset_paths)
            elif function_name == "var":
                variable = tinycss2.serialize(token.arguments).split(",", 1)[0].strip()
                if not _TOKEN_NAME_RE.fullmatch(variable):
                    raise ThemeValidationError("Safe CSS 只能读取 --pf-* 主题令牌。")
            _walk_css_tokens(token.arguments, asset_paths=asset_paths)
        content = getattr(token, "content", None)
        if content is not None:
            _walk_css_tokens(content, asset_paths=asset_paths)


def _validate_css_url(value: str, asset_paths: frozenset[str]) -> None:
    if value not in asset_paths or not value.startswith("assets/"):
        raise ThemeValidationError("Safe CSS URL 必须指向清单登记的本主题 assets/ 文件。")


def _selector_groups(tokens) -> list[list[Any]]:
    groups: list[list[Any]] = [[]]
    for token in tokens:
        if token.type == "literal" and token.value == ",":
            groups.append([])
        else:
            groups[-1].append(token)
    return groups


def _validate_selector(tokens) -> None:
    selector = tinycss2.serialize(tokens).replace(" ", "")
    match = _PART_SELECTOR_RE.fullmatch(selector)
    part = (match.group("double") or match.group("single")) if match else None
    if part not in REGISTERED_PARTS:
        raise ThemeValidationError("Safe CSS 选择器必须且只能作用于已注册 data-pf-part。")
    states = match.group("states")
    seen_states: set[str] = set()
    for state in _STATE_ATTRIBUTE_RE.finditer(states):
        name = state.group("name")
        value = state.group("double") or state.group("single")
        if name in seen_states or value not in DOCUMENTED_STATE_VALUES.get(name, frozenset()):
            raise ThemeValidationError("Safe CSS 选择器包含未登记或重复的状态属性。")
        seen_states.add(name)


def _validate_css_value(tokens, *, asset_paths: frozenset[str]) -> None:
    serialized = tinycss2.serialize(tokens)
    if len(serialized) > 1000 or any(character in serialized for character in "{};"):
        raise ThemeValidationError("Safe CSS 属性值无效或过长。")
    _walk_css_tokens(tokens, asset_paths=asset_paths)


def validate_safe_css(css_text: str, *, asset_paths: frozenset[str] = frozenset()) -> None:
    """使用 tinycss2 AST 校验选择器、声明和值，任何未知结构都拒绝。"""
    if len(css_text.encode("utf-8")) > 512 * 1024:
        raise ThemeValidationError("theme.css 超过允许大小。")
    rules = tinycss2.parse_stylesheet(css_text, skip_comments=True, skip_whitespace=True)
    if not rules:
        raise ThemeValidationError("theme.css 不得为空。")
    for rule in rules:
        if rule.type != "qualified-rule":
            raise ThemeValidationError("Safe CSS 不允许 @import、@font-face 或其他 at-rule。")
        groups = _selector_groups(rule.prelude)
        if not groups or any(not group for group in groups):
            raise ThemeValidationError("Safe CSS 选择器无效。")
        for group in groups:
            _validate_selector(group)
        declarations = tinycss2.parse_declaration_list(
            rule.content, skip_comments=True, skip_whitespace=True
        )
        if not declarations:
            raise ThemeValidationError("Safe CSS 规则不得为空。")
        for declaration in declarations:
            if declaration.type != "declaration" or declaration.important:
                raise ThemeValidationError("Safe CSS 声明无效，且不允许 !important。")
            name = declaration.lower_name
            if not (name in ALLOWED_CSS_PROPERTIES or _TOKEN_NAME_RE.fullmatch(name)):
                raise ThemeValidationError(f"Safe CSS 属性不在白名单中：{name}。")
            _validate_css_value(declaration.value, asset_paths=asset_paths)
            if name == "opacity":
                value = tinycss2.serialize(declaration.value).strip()
                try:
                    if not 0.35 <= float(value) <= 1:
                        raise ThemeValidationError("Safe CSS 不得把注册部件设为不可辨认。")
                except ValueError as error:
                    raise ThemeValidationError("opacity 必须是 0.35 到 1 的数字。") from error


def _validate_json_tree(value: Any, *, depth: int = 0) -> None:
    if depth > 5:
        raise ThemeValidationError("主题配置嵌套过深。")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > 300 or "javascript:" in value.lower() or "data:text/html" in value.lower():
            raise ThemeValidationError("主题配置包含不安全或过长字符串。")
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise ThemeValidationError("主题配置列表过长。")
        for item in value:
            _validate_json_tree(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 64:
            raise ThemeValidationError("主题配置对象字段过多。")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 80:
                raise ThemeValidationError("主题配置字段名无效。")
            _validate_json_tree(item, depth=depth + 1)
        return
    raise ThemeValidationError("主题配置只允许 JSON 基础类型。")


def validate_theme_config(config: dict[str, Any], *, file_paths: frozenset[str]) -> None:
    expected = {"accessibility", "appearance", "art", "charts", "components", "tokens"}
    if set(config) != expected:
        raise ThemeValidationError("theme.json 必须包含且只能包含规定的六个配置区段。")
    if config["appearance"] not in {"auto", "dark", "light"}:
        raise ThemeValidationError("appearance 必须是 auto、light 或 dark。")

    tokens = config["tokens"]
    if not isinstance(tokens, dict) or len(tokens) > 128:
        raise ThemeValidationError("tokens 必须是有限的对象。")
    for name, value in tokens.items():
        if not _TOKEN_NAME_RE.fullmatch(name) or not isinstance(value, str):
            raise ThemeValidationError("设计令牌必须使用 --pf-* 名称和字符串值。")
        parsed = tinycss2.parse_component_value_list(value)
        _validate_css_value(parsed, asset_paths=frozenset())

    art = config["art"]
    if not isinstance(art, dict) or set(art) != {"asset", "focus", "mode", "overlay", "safe_area"}:
        raise ThemeValidationError("art 配置字段不完整。")
    if art["mode"] not in {"ambient", "banner", "full", "off"}:
        raise ThemeValidationError("背景模式无效。")
    if art["safe_area"] not in {"auto", "center", "left", "none", "right"}:
        raise ThemeValidationError("背景安全区域无效。")
    focus = art["focus"]
    if not isinstance(focus, dict) or set(focus) != {"x", "y"}:
        raise ThemeValidationError("背景焦点必须包含 x 和 y。")
    if any(type(focus[key]) not in {int, float} or not 0 <= focus[key] <= 1 for key in ("x", "y")):
        raise ThemeValidationError("背景焦点坐标必须位于 0 到 1。")
    asset = art["asset"]
    if asset is not None and (asset not in file_paths or not str(asset).startswith("assets/")):
        raise ThemeValidationError("背景资源必须是清单登记的 assets/ 文件。")
    if not isinstance(art["overlay"], str):
        raise ThemeValidationError("背景遮罩必须是 CSS 字符串。")
    _validate_css_value(
        tinycss2.parse_component_value_list(art["overlay"]), asset_paths=frozenset()
    )

    components = config["components"]
    if not isinstance(components, dict) or any(part not in REGISTERED_PARTS for part in components):
        raise ThemeValidationError("components 只能引用注册部件。")
    for styles in components.values():
        if not isinstance(styles, dict):
            raise ThemeValidationError("组件结构化样式必须是对象。")
        for name, value in styles.items():
            if name not in ALLOWED_CSS_PROPERTIES or not isinstance(value, str):
                raise ThemeValidationError("组件结构化样式包含未知属性。")
            _validate_css_value(tinycss2.parse_component_value_list(value), asset_paths=frozenset())

    charts = config["charts"]
    allowed_chart_keys = {
        "backgroundColor",
        "categoryAxis",
        "color",
        "legend",
        "textStyle",
        "title",
        "tooltip",
        "valueAxis",
    }
    if not isinstance(charts, dict) or any(key not in allowed_chart_keys for key in charts):
        raise ThemeValidationError("charts 包含不支持的 ECharts 主题字段。")
    _validate_json_tree(charts)

    accessibility = config["accessibility"]
    if not isinstance(accessibility, dict) or set(accessibility) != {
        "high_contrast",
        "reduce_motion",
    }:
        raise ThemeValidationError("accessibility 配置字段不完整。")
    if type(accessibility["high_contrast"]) is not bool or accessibility["reduce_motion"] not in {
        "disable",
        "preserve",
        "reduce",
    }:
        raise ThemeValidationError("accessibility 配置值无效。")


def load_theme(root: Path, *, source: str, public_base_url: str) -> ThemeDescriptor:
    root = root.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ThemeValidationError("主题缺少普通文件 manifest.json。")
    manifest = _json_object(manifest_path)
    if set(manifest) != {
        "capabilities",
        "files",
        "id",
        "min_app_version",
        "name",
        "schema_version",
        "version",
    }:
        raise ThemeValidationError("manifest.json 字段不完整或包含未知字段。")
    if manifest["schema_version"] != THEME_SCHEMA_VERSION:
        raise ThemeValidationError("主题格式版本不受支持。")
    theme_id = manifest["id"]
    if (
        not isinstance(theme_id, str)
        or not _THEME_ID_RE.fullmatch(theme_id)
        or root.name != theme_id
    ):
        raise ThemeValidationError("主题 ID 无效或与目录名不一致。")
    if not isinstance(manifest["name"], str) or not 1 <= len(manifest["name"].strip()) <= 80:
        raise ThemeValidationError("主题名称无效。")
    _semver_triplet(manifest["version"])
    minimum = _semver_triplet(manifest["min_app_version"])
    if minimum > _semver_triplet(_application_version()):
        raise ThemeValidationError("主题要求更高版本的 Personal Finance。")
    capabilities = manifest["capabilities"]
    if not isinstance(capabilities, list) or len(capabilities) != len(set(capabilities)):
        raise ThemeValidationError("主题能力列表无效。")
    capability_set = frozenset(capabilities)
    unknown = capability_set - SUPPORTED_CAPABILITIES
    if unknown:
        raise ThemeValidationError(f"主题包含未知必需能力：{', '.join(sorted(unknown))}。")

    records = manifest["files"]
    if not isinstance(records, list) or not 1 <= len(records) <= 64:
        raise ThemeValidationError("主题文件清单无效。")
    file_paths: set[str] = set()
    total_size = 0
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
            raise ThemeValidationError("主题文件记录字段无效。")
        relative = _safe_relative_path(record["path"])
        relative_name = relative.as_posix()
        if relative_name in file_paths:
            raise ThemeValidationError("主题文件清单包含重复路径。")
        path = root.joinpath(*relative.parts)
        if not path.is_file() or path.is_symlink():
            raise ThemeValidationError(f"主题资源缺失：{relative_name}。")
        size = path.stat().st_size
        if type(record["size"]) is not int or record["size"] != size or size > 5 * 1024 * 1024:
            raise ThemeValidationError(f"主题资源大小校验失败：{relative_name}。")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not isinstance(record["sha256"], str) or record["sha256"] != digest:
            raise ThemeValidationError(f"主题资源哈希校验失败：{relative_name}。")
        total_size += size
        file_paths.add(relative_name)
    if total_size > 20 * 1024 * 1024:
        raise ThemeValidationError("主题资源总大小超过限制。")
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if actual_files != file_paths:
        raise ThemeValidationError("主题目录包含未登记资源或清单缺少资源。")
    if "theme.json" not in file_paths:
        raise ThemeValidationError("主题清单缺少 theme.json。")

    config = _json_object(root / "theme.json")
    frozen_paths = frozenset(file_paths)
    validate_theme_config(config, file_paths=frozen_paths)
    if "safe-css" in capability_set:
        if "theme.css" not in file_paths:
            raise ThemeValidationError("safe-css 主题缺少 theme.css。")
        try:
            css_text = (root / "theme.css").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ThemeValidationError("theme.css 必须是 UTF-8 文本。") from error
        validate_safe_css(
            css_text,
            asset_paths=frozenset(path for path in file_paths if path.startswith("assets/")),
        )
    if config["tokens"] and "tokens" not in capability_set:
        raise ThemeValidationError("主题使用了 tokens 配置但未声明对应能力。")
    if config["charts"] and "charts" not in capability_set:
        raise ThemeValidationError("主题使用了 charts 配置但未声明对应能力。")
    if config["art"]["asset"] and "background" not in capability_set:
        raise ThemeValidationError("主题使用了背景资源但未声明 background 能力。")

    manifest_bytes = manifest_path.read_bytes()
    revision = hashlib.sha256(manifest_bytes).hexdigest()[:16]
    stylesheet_url = ""
    if "theme.css" in file_paths:
        stylesheet_url = f"{public_base_url.rstrip('/')}/theme.css"
    preview_url = ""
    if "preview.webp" in file_paths:
        preview_url = f"{public_base_url.rstrip('/')}/preview.webp"
    return ThemeDescriptor(
        id=theme_id,
        name=manifest["name"].strip(),
        version=manifest["version"],
        capabilities=capability_set,
        source=source,
        root=root,
        revision=revision,
        stylesheet_url=stylesheet_url,
        preview_url=preview_url,
        config=config,
    )


def _emergency_safe_default() -> ThemeDescriptor:
    return ThemeDescriptor(
        id=SAFE_DEFAULT_THEME_ID,
        name="安全默认主题",
        version="0.0.0",
        capabilities=frozenset({"safe-css"}),
        source="emergency",
        root=None,
        revision="emergency",
        stylesheet_url=static("themes/safe-default/theme.css"),
        preview_url="",
        config={"appearance": "light", "charts": {}},
    )


class ThemeRegistry:
    def __init__(self) -> None:
        self._themes: dict[str, ThemeDescriptor] = {}
        self.errors: dict[str, str] = {}
        self._discover_root(Path(settings.THEME_BUILTIN_DIR), source="builtin", runtime_url=None)
        self._discover_root(
            Path(settings.THEME_RUNTIME_DIR),
            source="runtime",
            runtime_url=str(settings.THEME_RUNTIME_URL),
        )
        self._themes.setdefault(SAFE_DEFAULT_THEME_ID, _emergency_safe_default())

    def _discover_root(self, root: Path, *, source: str, runtime_url: str | None) -> None:
        if not root.is_dir():
            return
        for candidate in sorted(root.iterdir(), key=lambda path: path.name):
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            public_base_url = (
                f"{runtime_url.rstrip('/')}/{candidate.name}"
                if runtime_url is not None
                else static(f"themes/{candidate.name}").rstrip("/")
            )
            try:
                descriptor = load_theme(candidate, source=source, public_base_url=public_base_url)
            except (OSError, ThemeValidationError) as error:
                self.errors[candidate.name] = str(error)
                continue
            if descriptor.id in self._themes:
                self.errors[candidate.name] = "主题 ID 与更高优先级主题冲突。"
                continue
            self._themes[descriptor.id] = descriptor

    @property
    def themes(self) -> tuple[ThemeDescriptor, ...]:
        return tuple(
            sorted(self._themes.values(), key=lambda item: (item.source, item.name, item.id))
        )

    def get(self, theme_id: str) -> ThemeDescriptor | None:
        return self._themes.get(theme_id)

    def select(self, active_theme_id: str, last_known_good_theme_id: str) -> ThemeSelection:
        active = self.get(active_theme_id)
        if active is not None:
            return ThemeSelection(active, active_theme_id, "")
        last_good = self.get(last_known_good_theme_id)
        if last_good is not None:
            return ThemeSelection(last_good, active_theme_id, "active-theme-unavailable")
        return ThemeSelection(
            self._themes[SAFE_DEFAULT_THEME_ID], active_theme_id, "safe-default-fallback"
        )


def get_theme_registry() -> ThemeRegistry:
    # 注册器每次只扫描主题清单；导入后无需重启，且描述对象对单次请求保持不可变。
    return ThemeRegistry()
