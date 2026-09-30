"""Access-token storage. The token lives only in the macOS Keychain.

The engine never writes the token anywhere: not to files, git, logs, events,
dashboard snapshots or error messages. The owner adds it themselves:

    security add-generic-password -s lce-linkedin -a default -w

(`-w` without a value prompts for it, so it never appears in shell history.)
"""

from __future__ import annotations

import subprocess
from typing import Protocol

DEFAULT_SERVICE = "lce-linkedin"
DEFAULT_ACCOUNT = "default"


class CredentialError(RuntimeError):
    pass


class Secret:
    """Opaque token holder whose repr/str never reveal the value."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        if not value or not value.strip():
            raise CredentialError("empty token")
        self._value = value.strip()

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__


class TokenStore(Protocol):
    def exists(self) -> bool: ...

    def get(self) -> Secret: ...


class KeychainTokenStore:
    """Reads a generic password from the macOS login keychain via `security`."""

    def __init__(self, service: str = DEFAULT_SERVICE, account: str = DEFAULT_ACCOUNT,
                 runner=subprocess.run):
        self.service, self.account, self._run = service, account, runner

    def _cmd(self, *extra: str) -> list[str]:
        return ["security", "find-generic-password", "-s", self.service, "-a", self.account,
                *extra]

    def exists(self) -> bool:
        try:
            r = self._run(self._cmd(), capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0

    def get(self) -> Secret:
        try:
            r = self._run(self._cmd("-w"), capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CredentialError("macOS Keychain is not available") from exc
        if r.returncode != 0:
            raise CredentialError(
                f"no token in the Keychain (service {self.service!r}, account {self.account!r})")
        return Secret(r.stdout)


class MemoryTokenStore:
    """For tests only."""

    def __init__(self, value: str | None):
        self._value = value

    def exists(self) -> bool:
        return bool(self._value)

    def get(self) -> Secret:
        if not self._value:
            raise CredentialError("no token")
        return Secret(self._value)
