"""Turn responder boundary and initial adapters.

The thought-core loop depends on the TurnResponder boundary, not on a
particular LLM framework. LangChain, the OpenAI SDK, Dify as an internal
implementation candidate, or a local engine can all be added as adapters that
implement this small port.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib import error, request
from urllib.parse import urlparse

from .execution_deadline import clamp_execution_timeout, ensure_execution_active
from .persona import persona_system_prompt_from_env
from .schema import TurnInput


TURN_RESPONDER_BOUNDARY = "thought-core.turn_responder.v0"


@dataclass(frozen=True)
class ResponderResult:
    speech: str
    display: str
    status: str
    adapter_kind: str
    provider: str
    model: str
    used_llm: bool
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class TurnResponder(Protocol):
    adapter_kind: str
    provider: str
    model: str

    def respond(
        self,
        turn: TurnInput,
        *,
        response_context: Mapping[str, Any] | None = None,
    ) -> ResponderResult:
        """Return one assistant message for a turn."""


class LocalFallbackResponder:
    adapter_kind = "local_fallback"
    provider = "thought-core"
    model = "local-rule-v0"

    def respond(
        self,
        turn: TurnInput,
        *,
        status: str = "local_fallback",
        detail: str = "",
        response_context: Mapping[str, Any] | None = None,
    ) -> ResponderResult:
        text = turn.text.replace(" ", "")
        if any(marker in text for marker in ("音声", "マイク", "聞こえ", "聞き取")):
            speech = (
                "音声入力はテキストとして受け取れています。"
                "ただし、マイク音質やスピーカー出力は別の確認が必要です。"
            )
        elif "テスト" in text:
            speech = "入力は受け取れています。出力や機器状態の確認は別扱いです。"
        else:
            speech = (
                "入力は受け取りました。通常会話用LLMが未接続のため、"
                "今は簡易応答で返しています。"
            )
        return ResponderResult(
            speech=speech,
            display=speech,
            status=status,
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=False,
            detail=detail,
            metadata=_response_context_metadata(response_context),
        )


class OpenAICompatibleChatResponder:
    adapter_kind = "openai_compatible_chat"
    provider = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 12.0,
        max_chars: int = 220,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.max_chars = max(40, max_chars)

    @classmethod
    def from_env(cls) -> "OpenAICompatibleChatResponder | None":
        if _env_disabled("THOUGHT_CORE_LLM_ENABLED"):
            return None

        base_url = (
            os.environ.get("THOUGHT_CORE_LLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        api_key = os.environ.get("THOUGHT_CORE_LLM_API_KEY") or os.environ.get(
            "OPENAI_API_KEY", ""
        )
        model = (
            os.environ.get("THOUGHT_CORE_LLM_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "gpt-4o-mini"
        )
        timeout_s = _float_env("THOUGHT_CORE_LLM_TIMEOUT_S", 12.0)
        max_chars = _int_env("THOUGHT_CORE_LLM_MAX_CHARS", 220)

        if not api_key and not _is_loopback_url(base_url):
            return None

        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_s=timeout_s,
            max_chars=max_chars,
        )

    def respond(
        self,
        turn: TurnInput,
        *,
        response_context: Mapping[str, Any] | None = None,
    ) -> ResponderResult:
        persona_prompt = persona_system_prompt_from_env()
        system_prompt = (
            "You are the SWORD VOICE AGENT response adapter inside "
            "thought-core. Respect the boundary: return only a short "
            "Japanese assistant response for speech/display. Do not "
            "execute tools or claim device actions; home operations "
            "belong to the home-control tool boundary."
        )
        if persona_prompt:
            system_prompt = f"{system_prompt} {persona_prompt}"
        messages = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]
        context_text = _response_context_prompt(response_context)
        if context_text:
            messages.append({"role": "system", "content": context_text})
        messages.append({"role": "user", "content": turn.text})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": 180,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        ensure_execution_active()
        with request.urlopen(
            req,
            timeout=clamp_execution_timeout(self.timeout_s),
        ) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        ensure_execution_active()

        speech = _extract_chat_completion_text(response_payload)
        if not speech:
            raise ValueError("LLM response did not include message content")
        speech = _truncate(speech.strip(), self.max_chars)
        return ResponderResult(
            speech=speech,
            display=speech,
            status="llm_response",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=True,
            metadata={
                "base_url": _safe_base_url(self.base_url),
                **_response_context_metadata(response_context),
            },
        )


CommandRunner = Callable[[Sequence[str], float, str], subprocess.CompletedProcess[str]]
_SAFE_CODEX_CONFIG_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_CODEX_CONFIG_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:+/@-]+$")
_CODEX_CONFIG_KEY_ALLOWLIST = {
    "model_reasoning_effort",
    "model_verbosity",
}
_CODEX_RESPONSE_ENV_ALLOWLIST = frozenset(
    {
        "APPDATA",
        "CODEX_HOME",
        "COMSPEC",
        "HOME",
        "LOCALAPPDATA",
        "NO_COLOR",
        "OS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)


class CodexCliChatResponder:
    provider = "codex-cli"

    def __init__(
        self,
        *,
        command: str,
        model: str,
        cwd: Path,
        timeout_s: float = 60.0,
        max_chars: int = 220,
        profile: str = "",
        mode: str = "operate",
        sandbox: str = "",
        approval: str = "never",
        ephemeral: bool = True,
        config_overrides: Sequence[str] = (),
        expected_version: str = "",
        version_policy: str = "warn",
        version_timeout_s: float = 3.0,
        runner: CommandRunner | None = None,
    ) -> None:
        self.mode = _normalize_codex_mode(mode)
        self.adapter_kind = (
            "codex_cli_operator" if self.mode == "operate" else "codex_cli_responder"
        )
        self.command = command
        self.model = model
        self.cwd = cwd
        self.timeout_s = timeout_s
        self.max_chars = max(40, max_chars)
        self.profile = profile
        self.sandbox = _normalize_codex_sandbox(
            sandbox or ("workspace-write" if self.mode == "operate" else "read-only")
        )
        self.approval = _normalize_codex_approval(approval)
        self.sandbox = _safe_codex_child_sandbox(self.sandbox)
        self.approval = _safe_codex_child_approval(self.approval)
        self.ephemeral = ephemeral
        self.config_overrides = tuple(config_overrides)
        self.expected_version = expected_version.strip()
        self.version_policy = _normalize_codex_version_policy(version_policy)
        self.version_timeout_s = max(0.5, version_timeout_s)
        self.runner = runner

    @classmethod
    def from_env(cls) -> "CodexCliChatResponder | None":
        if _env_disabled("THOUGHT_CORE_LLM_ENABLED"):
            return None

        mode = os.environ.get("THOUGHT_CORE_CODEX_CLI_MODE") or "operate"
        normalized_mode = _normalize_codex_mode(mode)
        command = (
            os.environ.get("THOUGHT_CORE_CODEX_CLI_PATH")
            or os.environ.get("CODEX_CLI_PATH")
            or _default_codex_command()
        )
        cwd = Path(
            os.environ.get("THOUGHT_CORE_CODEX_CLI_CWD")
            or os.environ.get("THOUGHT_CORE_CODEX_CLI_WORKSPACE_ROOT")
            or os.environ.get("THOUGHT_CORE_CODEX_CLI_PROJECT_ROOT")
            or _default_codex_cwd()
        )
        model = (
            os.environ.get("THOUGHT_CORE_CODEX_CLI_MODEL")
            or os.environ.get("THOUGHT_CORE_LLM_MODEL")
            or "codex-cli"
        )
        profile = os.environ.get("THOUGHT_CORE_CODEX_CLI_PROFILE", "")
        timeout_s = _float_env("THOUGHT_CORE_CODEX_CLI_TIMEOUT_S", 60.0)
        max_chars = _int_env(
            "THOUGHT_CORE_CODEX_CLI_MAX_CHARS",
            900 if normalized_mode == "operate" else 220,
        )
        if max_chars == (900 if normalized_mode == "operate" else 220):
            max_chars = _int_env("THOUGHT_CORE_LLM_MAX_CHARS", max_chars)
        sandbox = os.environ.get("THOUGHT_CORE_CODEX_CLI_SANDBOX", "")
        approval = os.environ.get("THOUGHT_CORE_CODEX_CLI_APPROVAL", "never")
        ephemeral = _env_bool("THOUGHT_CORE_CODEX_CLI_EPHEMERAL", True)
        config_overrides = _codex_config_overrides_from_env()
        expected_version = os.environ.get(
            "THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION", ""
        )
        version_policy = os.environ.get("THOUGHT_CORE_CODEX_CLI_VERSION_POLICY", "warn")
        version_timeout_s = _float_env("THOUGHT_CORE_CODEX_CLI_VERSION_TIMEOUT_S", 3.0)

        return cls(
            command=command,
            model=model,
            cwd=cwd,
            timeout_s=timeout_s,
            max_chars=max_chars,
            profile=profile,
            mode=normalized_mode,
            sandbox=sandbox,
            approval=approval,
            ephemeral=ephemeral,
            config_overrides=config_overrides,
            expected_version=expected_version,
            version_policy=version_policy,
            version_timeout_s=version_timeout_s,
        )

    def respond(
        self,
        turn: TurnInput,
        *,
        response_context: Mapping[str, Any] | None = None,
    ) -> ResponderResult:
        version_metadata = self._codex_version_metadata()
        if (
            self.version_policy == "strict"
            and version_metadata["codex_cli_version_class"]
            != "observed_matching_expected"
        ):
            raise OSError(
                "Codex CLI version policy strict check failed: "
                f"{version_metadata['codex_cli_version_class']}"
            )

        with tempfile.TemporaryDirectory(prefix="thought-core-codex-") as tmp_dir:
            output_path = Path(tmp_dir) / "last-message.txt"
            effective_cwd = Path(tmp_dir) if self.mode == "respond" else self.cwd
            args = [
                *_codex_command_prefix(self.command),
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                self.sandbox,
                "--cd",
                str(effective_cwd),
                "--output-last-message",
                str(output_path),
            ]
            if self.ephemeral:
                args.insert(args.index("--skip-git-repo-check"), "--ephemeral")
            if self.mode == "respond":
                args.extend(
                    [
                        "--ignore-user-config",
                        "--ignore-rules",
                        "--disable",
                        "shell_tool",
                        "-c",
                        'web_search="disabled"',
                        "-c",
                        'shell_environment_policy.inherit="none"',
                    ]
                )
            for override in self.config_overrides:
                args.extend(["-c", override])
            args.extend(["-c", _codex_config_override("approval_policy", self.approval)])
            if self.model and self.model != "codex-cli":
                args.extend(["--model", self.model])
            if self.profile and self.mode == "operate":
                args.extend(["--profile", self.profile])
            prompt = _codex_cli_prompt(
                turn,
                response_context,
                mode=self.mode,
                sandbox=self.sandbox,
                approval=self.approval,
            )
            args.append("-")

            result = self._run(args, self.timeout_s, prompt)
            if result.returncode != 0:
                raise OSError(
                    "codex_cli_nonzero_exit:"
                    f"returncode_{result.returncode}"
                )

            speech = ""
            if output_path.exists():
                ensure_execution_active()
                speech = output_path.read_text(encoding="utf-8", errors="replace")
            if not speech:
                speech = result.stdout
            speech = _truncate(speech.strip(), self.max_chars)
            if not speech:
                raise ValueError("Codex CLI response did not include message content")

        return ResponderResult(
            speech=speech,
            display=speech,
            status="llm_response",
            adapter_kind=self.adapter_kind,
            provider=self.provider,
            model=self.model,
            used_llm=True,
            metadata={
                "cli": "codex",
                "codex_cli_mode": self.mode,
                "sandbox": self.sandbox,
                "approval": self.approval,
                "ephemeral": self.ephemeral,
                "workspace_class": (
                    "isolated_response_workspace"
                    if self.mode == "respond"
                    else _codex_workspace_class(effective_cwd)
                ),
                **version_metadata,
                **_response_context_metadata(response_context),
            },
        )

    def _codex_version_metadata(self) -> dict[str, str]:
        expected = _normalize_codex_cli_version(self.expected_version)
        metadata = {
            "codex_cli_version": "",
            "codex_cli_expected_version": expected,
            "codex_cli_version_class": "unavailable",
            "codex_cli_version_policy": self.version_policy,
        }
        try:
            args = [*_codex_command_prefix(self.command), "--version"]
            result = self._run(args, self.version_timeout_s, "")
        except (OSError, subprocess.SubprocessError, ValueError):
            return metadata

        if result.returncode != 0:
            return metadata

        observed = _normalize_codex_cli_version(
            (result.stdout or result.stderr or "").strip()
        )
        if not observed:
            return metadata

        metadata["codex_cli_version"] = observed
        if expected:
            metadata["codex_cli_version_class"] = (
                "observed_matching_expected"
                if observed == expected
                else "observed_mismatch_expected"
            )
        else:
            metadata["codex_cli_version_class"] = "observed_no_expected"
        return metadata

    def _run(
        self,
        args: Sequence[str],
        timeout_s: float,
        prompt: str,
    ) -> subprocess.CompletedProcess[str]:
        timeout_s = clamp_execution_timeout(timeout_s)
        ensure_execution_active()
        if self.runner is not None:
            result = self.runner(args, timeout_s, prompt)
            ensure_execution_active()
            return result
        child_env = (
            _codex_response_child_environment()
            if self.mode == "respond"
            else None
        )
        result = _run_codex_command(args, timeout_s, prompt, env=child_env)
        ensure_execution_active()
        return result


class EnvironmentTurnResponder:
    adapter_kind = "environment"
    provider = "thought-core"
    model = "configured"

    def __init__(
        self,
        primary: TurnResponder | None = None,
        fallback: LocalFallbackResponder | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback or LocalFallbackResponder()

    @classmethod
    def from_env(cls) -> "EnvironmentTurnResponder":
        if _env_disabled("THOUGHT_CORE_LLM_ENABLED"):
            return cls(primary=None)
        provider = _selected_llm_provider()
        if provider in {"codex", "codex-cli", "codex_cli"}:
            return cls(primary=CodexCliChatResponder.from_env())
        if provider in {
            "",
            "openai",
            "openai-compatible",
            "openai_compatible",
            "openai_compatible_chat",
        }:
            return cls(primary=OpenAICompatibleChatResponder.from_env())
        return cls(primary=None)

    def respond(
        self,
        turn: TurnInput,
        *,
        response_context: Mapping[str, Any] | None = None,
    ) -> ResponderResult:
        if self.primary is None:
            return self.fallback.respond(
                turn,
                status="local_fallback_no_llm_adapter",
                detail="No LLM responder adapter is configured.",
                response_context=response_context,
            )
        try:
            return self.primary.respond(turn, response_context=response_context)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            error.URLError,
            subprocess.SubprocessError,
        ) as exc:
            ensure_execution_active()
            detail = (
                _codex_cli_safe_exception_detail(exc)
                if isinstance(self.primary, CodexCliChatResponder)
                else _truncate(str(exc), 240)
            )
            return self.fallback.respond(
                turn,
                status="local_fallback_after_llm_error",
                detail=detail,
                response_context=response_context,
            )


def describe_responder(responder: TurnResponder) -> dict[str, str]:
    return {
        "boundary": TURN_RESPONDER_BOUNDARY,
        "adapter_kind": getattr(responder, "adapter_kind", "unknown"),
        "provider": getattr(responder, "provider", "unknown"),
        "model": getattr(responder, "model", "unknown"),
    }


def _extract_chat_completion_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    text = first.get("text")
    return text if isinstance(text, str) else ""


def _env_disabled(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.strip().lower() in {"0", "false", "off", "no"}


def _selected_llm_provider() -> str:
    return (
        os.environ.get("THOUGHT_CORE_LLM_PROVIDER")
        or os.environ.get("THOUGHT_CORE_LLM_ADAPTER")
        or ""
    ).strip().lower()


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "on", "yes"}:
        return True
    if lowered in {"0", "false", "off", "no"}:
        return False
    return default


def _normalize_codex_mode(value: str) -> str:
    text = value.strip().lower().replace("_", "-")
    if text in {"respond", "response", "chat", "speech"}:
        return "respond"
    return "operate"


def _normalize_codex_sandbox(value: str) -> str:
    text = value.strip().lower()
    if text in {"read-only", "workspace-write", "danger-full-access"}:
        return text
    return "workspace-write"


def _normalize_codex_approval(value: str) -> str:
    text = value.strip().lower()
    if text in {"never", "on-request", "untrusted"}:
        return text
    return "never"


def _safe_codex_child_sandbox(value: str) -> str:
    if value == "danger-full-access":
        return "workspace-write"
    return value


def _safe_codex_child_approval(value: str) -> str:
    return "never"


def _codex_cli_safe_exception_detail(exc: BaseException) -> str:
    text = str(exc).lower()
    if isinstance(exc, subprocess.TimeoutExpired):
        return "codex_cli_failure_class:timeout"
    if isinstance(exc, FileNotFoundError):
        return "codex_cli_failure_class:unavailable"
    if isinstance(exc, PermissionError) or "permission" in text or "access is denied" in text:
        return "codex_cli_failure_class:permission_denied"
    if "codex_cli_nonzero_exit" in text:
        return "codex_cli_failure_class:nonzero_returncode"
    if isinstance(exc, ValueError):
        return "codex_cli_failure_class:invalid_response"
    if isinstance(exc, subprocess.SubprocessError):
        return "codex_cli_failure_class:subprocess_error"
    if isinstance(exc, OSError):
        return "codex_cli_failure_class:unavailable"
    return "codex_cli_failure_class:unknown"


def _normalize_codex_version_policy(value: str) -> str:
    text = value.strip().lower()
    if text in {"strict", "fail", "enforce"}:
        return "strict"
    return "warn"


def _normalize_codex_cli_version(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    match = re.search(r"\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9_.-]+)?", first_line)
    if match:
        return match.group(0)
    safe = re.sub(r"[^A-Za-z0-9_.:+/@ -]+", "", first_line)
    return _truncate(safe.strip(), 80)


def _codex_config_overrides_from_env() -> list[str]:
    overrides: list[str] = []
    reasoning_effort = (
        os.environ.get("THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT")
        or os.environ.get("THOUGHT_CORE_CODEX_CLI_MODEL_REASONING_EFFORT")
        or ""
    ).strip()
    if reasoning_effort:
        overrides.append(_codex_config_override("model_reasoning_effort", reasoning_effort))

    verbosity = os.environ.get("THOUGHT_CORE_CODEX_CLI_VERBOSITY", "").strip()
    if verbosity:
        overrides.append(_codex_config_override("model_verbosity", verbosity))

    for raw_item in _split_codex_config_overrides(
        os.environ.get("THOUGHT_CORE_CODEX_CLI_CONFIG_OVERRIDES", "")
    ):
        key, value = raw_item
        if key not in _CODEX_CONFIG_KEY_ALLOWLIST:
            continue
        overrides.append(_codex_config_override(key, value))
    return overrides


def _split_codex_config_overrides(value: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for raw_item in re.split(r"[;\n]", value or ""):
        item = raw_item.strip()
        if not item or "=" not in item:
            continue
        key, raw_value = item.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip().strip('"').strip("'")
        if key and raw_value:
            result.append((key, raw_value))
    return result


def _codex_config_override(key: str, value: str) -> str:
    if not _SAFE_CODEX_CONFIG_KEY_RE.fullmatch(key):
        raise ValueError("Codex CLI config key is not safe")
    if not _SAFE_CODEX_CONFIG_VALUE_RE.fullmatch(value):
        raise ValueError("Codex CLI config value is not safe")
    return f'{key}="{value}"'


def _default_codex_command() -> str:
    npm_bin = Path(os.environ.get("APPDATA", "")) / "npm"
    for candidate in (
        shutil.which("codex.cmd"),
        shutil.which("codex.exe"),
        shutil.which("codex"),
        str(npm_bin / "codex.cmd"),
        str(npm_bin / "codex.exe"),
        str(npm_bin / "codex.ps1"),
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return "codex"


def _default_codex_cwd() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "AGENTS.md").is_file() and (parent / "control-plane").is_dir():
            return parent
    return Path(os.getcwd())


def _codex_workspace_class(path: Path) -> str:
    if (path / "AGENTS.md").is_file() and (path / "control-plane").is_dir():
        return "sword_agent_os_system_root"
    if (path / "pyproject.toml").is_file() or (path / "package.json").is_file():
        return "project_root"
    return "configured_workspace"


def _codex_command_prefix(command: str) -> list[str]:
    lower_command = command.lower()
    if lower_command.endswith(".ps1"):
        powershell = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            command,
        ]
    if lower_command.endswith((".cmd", ".bat")):
        command_shell = (
            os.environ.get("COMSPEC")
            or shutil.which("cmd.exe")
            or str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "cmd.exe")
        )
        return [command_shell, "/d", "/s", "/c", command]
    return [command]


def _run_codex_command(
    args: Sequence[str],
    timeout_s: float,
    prompt: str,
    *,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_kwargs: dict[str, Any] = {
        "check": False,
        "capture_output": True,
        "input": prompt,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout_s,
    }
    if env is not None:
        run_kwargs["env"] = dict(env)
    return subprocess.run(list(args), **run_kwargs)


def _codex_response_child_environment() -> dict[str, str]:
    child_env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.upper() in _CODEX_RESPONSE_ENV_ALLOWLIST:
            child_env[key] = value
    return child_env


def _codex_cli_prompt(
    turn: TurnInput,
    response_context: Mapping[str, Any] | None,
    *,
    mode: str,
    sandbox: str,
    approval: str,
) -> str:
    persona_prompt = persona_system_prompt_from_env()
    context_text = _response_context_prompt(response_context)
    parts = [
        "You are Codex CLI embedded inside Thought Core, not a parallel "
        "AITuberKit provider. AITuberKit, VOICEVOX, memory, Environment State, "
        "and Home Control remain behind the existing Thought Core boundaries.",
        f"Execution mode: {mode}. Sandbox: {sandbox}. Approval: {approval}.",
    ]
    if mode == "operate":
        parts.extend(
            [
                "Your working directory is the SWORD Agent OS system workspace. "
                "Read and obey AGENTS.md and narrower project rules before "
                "changing files.",
                "Act as a self-operating development agent when the user asks "
                "for system work: inspect files, make tightly scoped edits, "
                "and run deterministic validation when useful.",
                "Do not revert unrelated user or route-owned changes. Do not "
                "modify persistent machine settings. Keep Home Assistant and "
                "appliance operations inside the existing Home Control action "
                "boundary; do not bypass it through shell or direct APIs.",
                "Return a Japanese avatar-facing final message. For ordinary "
                "conversation keep it natural; for development work include "
                "the concrete change/result/test status and any blocker.",
            ]
        )
    else:
        parts.extend(
            [
                "Operate as a response-only adapter in an isolated empty working "
                "directory. Shell, web, user configuration, project rules, and "
                "external tools are technically unavailable for this turn.",
                "Do not claim file, command, network, device, or Home Control "
                "actions. Thought Core has already decided any allowed action; "
                "you may vary only its user-facing wording.",
                "Return only the final short Japanese assistant utterance for "
                "speech and display. Do not include analysis, logs, markdown "
                "fences, or tool call descriptions.",
            ]
        )
    if persona_prompt:
        parts.append(f"Persona and expression guidance: {persona_prompt}")
    if context_text:
        parts.append(context_text)
    parts.append(f"User turn:\n{turn.text}")
    return "\n\n".join(parts)


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


def _is_loopback_url(value: str) -> bool:
    host = urlparse(value).hostname or ""
    return host == "localhost" or host == "::1" or host.startswith("127.")


def _safe_base_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    return f"{parsed.scheme}://{parsed.hostname or parsed.netloc}"


def _response_context_metadata(
    response_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(response_context, Mapping):
        return {"response_context_used": False}
    return {
        "response_context_used": True,
        "response_context_keys": sorted(str(key) for key in response_context.keys()),
    }


def _response_context_prompt(response_context: Mapping[str, Any] | None) -> str:
    if not isinstance(response_context, Mapping):
        return ""
    compact = {
        key: response_context.get(key)
        for key in (
            "previous_fragment",
            "issue_key",
            "recent_fragments",
            "current_stage",
            "action_id",
            "target",
            "response_goal",
            "semantic_draft",
            "display_draft",
            "required_facts",
            "forbidden_claims",
            "visible_phrase_contract",
        )
        if response_context.get(key)
    }
    if not compact:
        return ""
    return (
        "Compact response context for wording continuity only. "
        "Use it to avoid repetitive phrasing and to render the requested "
        "visible phrase in natural Japanese. Do not treat it as a tool result "
        "or permission to execute actions. If semantic_draft is present, treat "
        "it as facts to express, not as text to copy verbatim. Avoid starting "
        "with stock acknowledgements like 了解 when this is not the immediate "
        "reflex acknowledgement. Avoid repeating the same device name when the "
        "previous phrase already named it: "
        f"{json.dumps(compact, ensure_ascii=False, sort_keys=True)}"
    )


def _truncate(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else f"{value[:max_chars]}..."
