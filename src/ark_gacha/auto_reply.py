"""File-backed, bounded regex replies. No incoming message bodies are logged."""

import asyncio
import hashlib
import logging
import time
import tomllib
from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import regex
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import UserError

log = logging.getLogger(__name__)
MAX_FILE_BYTES = 256 * 1024
MAX_TEXT_LENGTH = 8192
MATCH_TIMEOUT = 0.01
MESSAGE_TIMEOUT = 0.05
DEDUP_SECONDS = 600
MAX_TRACKED = 10000


class AutoReplyConfigError(UserError):
    pass


class RuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    enabled: bool = True
    pattern: str = Field(min_length=1, max_length=2048)
    mode: Literal["search", "fullmatch"] = "search"
    reply: str = Field(max_length=4000)
    at_sender: bool = False
    require_at_self: bool = False
    cooldown_seconds: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)
    groups: list[Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]] = Field(default_factory=list)


class FileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    enabled: bool = True
    reload_interval_seconds: float = Field(default=5, ge=0.1, le=300, allow_inf_nan=False)
    default_cooldown_seconds: float = Field(default=30, ge=0, le=86400, allow_inf_nan=False)
    rules: list[RuleConfig] = Field(max_length=200)


@dataclass(frozen=True)
class ReplyRule:
    id: str
    pattern: regex.Pattern
    mode: str
    reply: str
    at_sender: bool
    cooldown_seconds: float
    groups: frozenset[str]
    require_at_self: bool = False


@dataclass(frozen=True)
class RuleSet:
    enabled: bool = False
    reload_interval_seconds: float = 5
    rules: tuple[ReplyRule, ...] = ()


def read_rules_file(path: Path) -> bytes:
    with path.open("rb") as source:
        body = source.read(MAX_FILE_BYTES + 1)
    if len(body) > MAX_FILE_BYTES:
        raise AutoReplyConfigError("规则文件不能超过 256 KiB。")
    return body


def parse_rules(body: bytes) -> RuleSet:
    try:
        raw = tomllib.loads(body.decode("utf-8-sig"))
    except (UnicodeError, tomllib.TOMLDecodeError, RecursionError) as exc:
        raise AutoReplyConfigError(f"规则文件必须是有效的 UTF-8 TOML：{exc}") from None
    try:
        config = FileConfig.model_validate(raw)
    except ValidationError as exc:
        details = []
        for error in exc.errors(include_input=False, include_url=False):
            location = error["loc"]
            label = ".".join(str(part) for part in location)
            if len(location) > 1 and location[0] == "rules":
                candidate = raw["rules"][location[1]]
                rule_id = candidate.get("id") if isinstance(candidate, dict) else None
                if isinstance(rule_id, str) and regex.fullmatch(r"[A-Za-z0-9_-]{1,64}", rule_id):
                    label = f"规则 {rule_id}: {label}"
            details.append(f"{label}: {error['msg']}")
        raise AutoReplyConfigError("配置错误：" + "; ".join(details)) from None
    # Literal[1] also accepts True and 1.0; the schema version must be an integer.
    if type(raw["version"]) is not int:
        raise AutoReplyConfigError("version 必须为整数 1。")
    rules = []
    ids = set()
    for item in config.rules:
        if item.id in ids:
            raise AutoReplyConfigError(f"规则 ID 重复：{item.id}")
        ids.add(item.id)
        try:
            pattern = regex.compile(item.pattern, regex.VERSION0)
        except (regex.error, RecursionError) as exc:
            raise AutoReplyConfigError(f"规则 {item.id}: 正则错误：{exc}") from None
        if item.enabled:
            rules.append(
                ReplyRule(
                    item.id,
                    pattern,
                    item.mode,
                    item.reply,
                    item.at_sender,
                    config.default_cooldown_seconds
                    if item.cooldown_seconds is None
                    else item.cooldown_seconds,
                    frozenset(item.groups),
                    item.require_at_self,
                )
            )
    return RuleSet(config.enabled, config.reload_interval_seconds, tuple(rules))


def load_rules(path: Path) -> RuleSet:
    return parse_rules(read_rules_file(path))


def first_match(rules: RuleSet, group: str, text: str, at_self: bool = False) -> ReplyRule | None:
    deadline = time.monotonic() + MESSAGE_TIMEOUT
    for rule in rules.rules:
        if not rule.reply.strip() or (rule.require_at_self and not at_self):
            continue
        if rule.groups and group not in rule.groups:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.warning("Auto-reply message matching budget exceeded")
            return None
        try:
            match = getattr(rule.pattern, rule.mode)(
                text, timeout=min(MATCH_TIMEOUT, remaining), concurrent=True
            )
        except TimeoutError:
            log.warning("Auto-reply regex timed out (rule=%s)", rule.id)
            return None
        if match is not None:
            return rule
    return None


class AutoReplyService:
    def __init__(
        self,
        path: Path,
        allowed_groups: set[str],
        clock: Callable[[], float] = time.monotonic,
    ):
        self.path = path
        self.allowed_groups = frozenset(allowed_groups)
        self.clock = clock
        self.ruleset = RuleSet()
        self._last_attempt: object = None
        self._reload_lock = asyncio.Lock()
        self._workers = asyncio.Semaphore(2)
        self._watcher: asyncio.Task | None = None
        self._seen: OrderedDict[tuple[str, str, str], float] = OrderedDict()
        self._cooldowns: dict[tuple[str, str, str], float] = {}
        self._next_prune = 0.0

    async def reload(self) -> bool:
        async with self._reload_lock:
            try:
                body = await asyncio.to_thread(read_rules_file, self.path)
            except (OSError, AutoReplyConfigError) as exc:
                token = (type(exc).__name__, getattr(exc, "errno", None))
                if token != self._last_attempt:
                    if not isinstance(exc, FileNotFoundError) or self._last_attempt is not None:
                        log.warning("Auto-reply reload failed; keeping previous rules: %s", exc)
                    self._last_attempt = token
                return False
            token = hashlib.sha256(body).digest()
            if token == self._last_attempt:
                return False
            self._last_attempt = token
            try:
                candidate = await asyncio.to_thread(parse_rules, body)
            except AutoReplyConfigError as exc:
                log.warning("Auto-reply reload failed; keeping previous rules: %s", exc)
                return False
            self.ruleset = candidate
            active_ids = {rule.id for rule in candidate.rules}
            self._cooldowns = {
                key: expiry for key, expiry in self._cooldowns.items() if key[2] in active_ids
            }
            log.info(
                "Auto-reply loaded (%d rules, enabled=%s)", len(candidate.rules), candidate.enabled
            )
            return True

    async def start(self) -> None:
        if self._watcher is not None:
            return
        await self.reload()

        async def watch():
            while True:
                await asyncio.sleep(self.ruleset.reload_interval_seconds)
                await self.reload()

        self._watcher = asyncio.create_task(watch())

    async def close(self) -> None:
        if self._watcher:
            self._watcher.cancel()
            with suppress(asyncio.CancelledError):
                await self._watcher
            self._watcher = None
        self.ruleset = RuleSet()
        self._last_attempt = None
        self._seen.clear()
        self._cooldowns.clear()

    def _prune(self, now: float) -> None:
        while self._seen and next(iter(self._seen.values())) <= now:
            self._seen.popitem(last=False)
        if now >= self._next_prune or len(self._cooldowns) >= MAX_TRACKED:
            self._cooldowns = {key: end for key, end in self._cooldowns.items() if end > now}
            self._next_prune = now + 30

    async def reply_for(
        self,
        bot_id: str,
        group: str,
        user: str,
        message_id: str,
        text: str,
        *,
        at_self: bool = False,
    ) -> ReplyRule | None:
        snapshot = self.ruleset
        text = text.strip()
        if (
            not snapshot.enabled
            or group not in self.allowed_groups
            or user == bot_id
            or not text
            or len(text) > MAX_TEXT_LENGTH
        ):
            return None
        now = self.clock()
        self._prune(now)
        event_key = (bot_id, group, message_id)
        if event_key in self._seen:
            return None
        if len(self._seen) >= MAX_TRACKED:
            # Keep the full dedup window even under a burst; do not evict live entries.
            return None
        self._seen[event_key] = now + DEDUP_SECONDS
        if self._workers.locked():
            return None
        async with self._workers:
            rule = await asyncio.to_thread(first_match, snapshot, group, text, at_self)
        # A reload/disable while matching must not send from an obsolete snapshot.
        if rule is None or snapshot is not self.ruleset:
            return None
        now = self.clock()
        key = (bot_id, group, rule.id)
        if self._cooldowns.get(key, 0) > now:
            return None
        if rule.cooldown_seconds:
            if key not in self._cooldowns and len(self._cooldowns) >= MAX_TRACKED:
                return None
            # No await between checking and reserving: concurrent messages cannot double-send.
            self._cooldowns[key] = now + rule.cooldown_seconds
        return rule
