"""A tiny semver + range parser, because a plugin manifest must not pull a dependency.

`docs/socket-plug.md` writes compatibility as a space-separated comparator list
(``">=0.1 <0.3"``) and versions as semver. This module implements exactly that,
plus the usual ``>``, ``<=``, ``==``/``=`` and ``!=`` comparators and comma
separators. Build metadata is accepted and ignored for precedence, as semver
says.

Partial versions are allowed in a range because the standard uses ``0.1`` and
``0.3``: a missing minor/patch is zero. A plugin's own ``version`` is parsed
with ``allow_partial=False`` so ``1.2`` is rejected where semver is promised.

Supported range grammar::

    range      := comparator (("," | whitespace) comparator)*
    comparator := ("*" | "x" | "X") | (op? partial-version)
    op         := ">=" | "<=" | ">" | "<" | "==" | "=" | "!="
    partial-version := "v"? N ("." N ("." N prerelease? build?)?)?

There is no OR. An unsupported operator (``||``, ``^``, ``~``) is an error with
a readable message instead of a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

__all__ = [
    "Comparator",
    "Version",
    "VersionError",
    "VersionRange",
    "parse_range",
    "parse_version",
    "satisfies",
]


class VersionError(ValueError):
    """A version or range string is not understood."""


_VERSION_RE = re.compile(
    r"^[vV]?"
    r"(?P<major>\d+)"
    r"(?:\.(?P<minor>\d+))?"
    r"(?:\.(?P<patch>\d+))?"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_COMPARATOR_RE = re.compile(r"^(>=|<=|==|!=|>|<|=)?(.*)$")
_OPERATORS = (">=", "<=", "==", "!=", ">", "<", "=")


def _no_leading_zero(part: str, what: str, text: str) -> int:
    if len(part) > 1 and part.startswith("0"):
        raise VersionError(f"invalid version {text!r}: {what} has a leading zero")
    return int(part)


@total_ordering
@dataclass(frozen=True)
class Version:
    """One parsed semver. Build metadata is intentionally not stored."""

    major: int
    minor: int = 0
    patch: int = 0
    prerelease: tuple[str, ...] = ()

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            return f"{base}-{'.'.join(self.prerelease)}"
        return base

    def _precedence_key(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        if self._precedence_key() != other._precedence_key():
            return self._precedence_key() < other._precedence_key()
        return _prerelease_lt(self.prerelease, other.prerelease)


def _prerelease_lt(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Semver 11.4 precedence for prerelease identifiers."""
    if not left:
        return False          # a release is greater than any prerelease
    if not right:
        return True
    for a, b in zip(left, right, strict=False):
        if a == b:
            continue
        a_numeric, b_numeric = a.isdigit(), b.isdigit()
        if a_numeric and b_numeric:
            return int(a) < int(b)
        if a_numeric != b_numeric:
            return a_numeric          # numeric identifiers sort before alphanumeric
        return a < b
    return len(left) < len(right)     # a prefix sorts before the longer list


def parse_version(text: str, *, allow_partial: bool = False) -> Version:
    """Parse ``x.y.z`` (or ``x`` / ``x.y`` when ``allow_partial``) into a Version."""
    if not isinstance(text, str):
        raise VersionError(f"version must be a string, got {type(text).__name__}")
    raw = text.strip()
    if not raw:
        raise VersionError("version is empty; expected something like 1.2.0")
    match = _VERSION_RE.fullmatch(raw)
    if match is None:
        raise VersionError(f"invalid version {text!r}; expected semver like 1.2.0")
    if not allow_partial and (match.group("minor") is None or match.group("patch") is None):
        raise VersionError(f"invalid version {text!r}; expected a full semver x.y.z")
    if (match.group("pre") is not None or match.group("build") is not None) and (
        match.group("patch") is None
    ):
        raise VersionError(
            f"invalid version {text!r}: a prerelease/build suffix needs x.y.z"
        )
    major = _no_leading_zero(match.group("major"), "major", text)
    minor = (
        _no_leading_zero(match.group("minor"), "minor", text)
        if match.group("minor") is not None
        else 0
    )
    patch = (
        _no_leading_zero(match.group("patch"), "patch", text)
        if match.group("patch") is not None
        else 0
    )
    prerelease = tuple(match.group("pre").split(".")) if match.group("pre") else ()
    for identifier in prerelease:
        if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
            raise VersionError(
                f"invalid version {text!r}: numeric prerelease identifiers have no leading zero"
            )
    return Version(major=major, minor=minor, patch=patch, prerelease=prerelease)


@dataclass(frozen=True)
class Comparator:
    op: str
    version: Version

    def matches(self, version: Version) -> bool:
        if self.op == ">=":
            return version >= self.version
        if self.op == "<=":
            return version <= self.version
        if self.op == ">":
            return version > self.version
        if self.op == "<":
            return version < self.version
        if self.op == "!=":
            return version != self.version
        return version == self.version   # "==", "=" and a bare version


@dataclass(frozen=True)
class VersionRange:
    """A conjunction of comparators, e.g. ``>=0.1 <0.3``."""

    text: str
    comparators: tuple[Comparator, ...] = ()

    def matches(self, version: Version | str) -> bool:
        candidate = parse_version(version) if isinstance(version, str) else version
        return all(comparator.matches(candidate) for comparator in self.comparators)


def _tokens(text: str) -> list[str]:
    """Split a range, joining an operator that was separated from its version."""
    raw_tokens = text.replace(",", " ").split()
    joined: list[str] = []
    index = 0
    while index < len(raw_tokens):
        token = raw_tokens[index]
        if token in _OPERATORS and index + 1 < len(raw_tokens):
            joined.append(token + raw_tokens[index + 1])
            index += 2
        else:
            joined.append(token)
            index += 1
    return joined


def parse_range(text: str) -> VersionRange:
    """Parse a range string; raises VersionError with the offending token."""
    if not isinstance(text, str):
        raise VersionError(f"version range must be a string, got {type(text).__name__}")
    raw = text.strip()
    if not raw:
        raise VersionError("version range is empty; expected something like '>=0.1 <0.3'")
    comparators: list[Comparator] = []
    for token in _tokens(raw):
        if token in {"*", "x", "X"}:
            continue
        match = _COMPARATOR_RE.fullmatch(token)
        # The regex matches every token; a missing version is the real error.
        assert match is not None
        op = match.group(1) or "=="
        version_text = match.group(2)
        if not version_text:
            raise VersionError(f"invalid version range {text!r}: {op!r} has no version")
        try:
            version = parse_version(version_text, allow_partial=True)
        except VersionError as exc:
            raise VersionError(f"invalid version range {text!r}: {exc}") from None
        comparators.append(Comparator(op=op, version=version))
    if not comparators and not _tokens(raw):
        raise VersionError(f"invalid version range {text!r}: no comparators found")
    return VersionRange(text=raw, comparators=tuple(comparators))


def satisfies(version: str, range_text: str) -> bool:
    """``satisfies("0.1.0", ">=0.1 <0.3")`` -> True."""
    return parse_range(range_text).matches(parse_version(version))
