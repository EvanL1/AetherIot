"""Shared artifact bundle governance helpers for forecast backends."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_BUNDLE_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_BUNDLE_DOMAIN = b"aether.artifact.bundle.v1\x00"


class EngineUnavailable(RuntimeError):
    """The injected model engine cannot produce an approved forecast."""


@dataclass(frozen=True, slots=True)
class _FileFingerprint:
    device: int
    inode: int
    size: int
    modified_ns: int


def normalize_bundle_files(files: Mapping[str, str | Path]) -> dict[str, Path]:
    if not files or len(files) > 64:
        raise ValueError("artifact bundle must declare between 1 and 64 files")
    normalized: dict[str, Path] = {}
    resolved_paths: set[Path] = set()
    for logical_name, configured_path in files.items():
        if (
            not isinstance(logical_name, str)
            or not _BUNDLE_FILE_NAME.fullmatch(logical_name)
            or ".." in Path(logical_name).parts
        ):
            raise ValueError("artifact bundle logical file name is invalid")
        if not isinstance(configured_path, (str, Path)):
            raise ValueError("artifact bundle path is invalid")
        path = Path(configured_path)
        if not path.is_absolute():
            raise ValueError("artifact bundle paths must be absolute")
        if path.is_symlink():
            raise ValueError("artifact bundle paths must not be symbolic links")
        try:
            metadata = path.stat()
        except OSError as exc:
            raise ValueError("artifact bundle file is unavailable") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("artifact bundle paths must name regular files")
        resolved = path.resolve(strict=True)
        if resolved in resolved_paths:
            raise ValueError("artifact bundle paths must be unique")
        resolved_paths.add(resolved)
        normalized[logical_name] = path
    return normalized


def _hash_bundle_files(files: Mapping[str, Path]) -> tuple[str, dict[str, _FileFingerprint]]:
    hasher = hashlib.sha256()
    hasher.update(_BUNDLE_DOMAIN)
    fingerprints: dict[str, _FileFingerprint] = {}
    for logical_name in sorted(files):
        path = files[logical_name]
        encoded_name = logical_name.encode("utf-8")
        hasher.update(len(encoded_name).to_bytes(4, "big"))
        hasher.update(encoded_name)
        try:
            with path.open("rb") as artifact_file:
                before = os.fstat(artifact_file.fileno())
                hasher.update(before.st_size.to_bytes(8, "big"))
                while chunk := artifact_file.read(1024 * 1024):
                    hasher.update(chunk)
                after = os.fstat(artifact_file.fileno())
        except OSError as exc:
            raise ValueError("artifact bundle file could not be hashed") from exc
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity:
            raise ValueError("artifact bundle file changed while it was hashed")
        fingerprints[logical_name] = _FileFingerprint(*after_identity)
    return f"sha256:{hasher.hexdigest()}", fingerprints


def compute_artifact_bundle_digest(files: Mapping[str, str | Path]) -> str:
    normalized = normalize_bundle_files(files)
    digest, _fingerprints = _hash_bundle_files(normalized)
    return digest


@dataclass(frozen=True, slots=True)
class CommissionedArtifactBundle:
    """A locally verified immutable set of files used by one model version."""

    files: Mapping[str, Path | str]
    expected_digest: str
    actual_digest: str = field(init=False)
    _fingerprints: Mapping[str, _FileFingerprint] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not _DIGEST.fullmatch(self.expected_digest):
            raise ValueError("commissioned artifact bundle digest is invalid")
        normalized = normalize_bundle_files(self.files)
        actual_digest, fingerprints = _hash_bundle_files(normalized)
        if not hmac.compare_digest(actual_digest, self.expected_digest):
            raise ValueError("commissioned artifact bundle digest does not match its files")
        object.__setattr__(self, "files", MappingProxyType(normalized))
        object.__setattr__(self, "actual_digest", actual_digest)
        object.__setattr__(self, "_fingerprints", MappingProxyType(fingerprints))

    def verify_unchanged(self) -> None:
        fingerprints = self._fingerprints
        for logical_name, path in self.files.items():
            try:
                if path.is_symlink():
                    raise EngineUnavailable("artifact bundle changed after commissioning")
                metadata = path.stat()
            except OSError:
                raise EngineUnavailable("artifact bundle changed after commissioning") from None
            current = _FileFingerprint(
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
            if current != fingerprints[logical_name]:
                raise EngineUnavailable("artifact bundle changed after commissioning")


def load_commissioned_artifact_bundles_from_env(
    env_name: str,
    *,
    required: bool = True,
) -> dict[tuple[str, str, str], CommissionedArtifactBundle]:
    raw = os.getenv(env_name)
    if not raw:
        if required:
            raise ValueError(f"{env_name} is required")
        return {}
    try:
        declarations = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{env_name} must contain valid JSON") from exc
    if not isinstance(declarations, list) or not declarations or len(declarations) > 128:
        raise ValueError(f"{env_name} must be a non-empty JSON array")
    expected_fields = {"kind", "family", "version", "expected_digest", "files"}
    bundles: dict[tuple[str, str, str], CommissionedArtifactBundle] = {}
    for declaration in declarations:
        if not isinstance(declaration, dict) or set(declaration) != expected_fields:
            raise ValueError("artifact bundle declaration has invalid fields")
        kind = declaration["kind"]
        family = declaration["family"]
        version = declaration["version"]
        digest = declaration["expected_digest"]
        files = declaration["files"]
        if (
            not all(isinstance(value, str) and value.strip() for value in (kind, family, version))
            or not isinstance(digest, str)
            or not isinstance(files, dict)
            or any(
                not isinstance(name, str) or not isinstance(path, str)
                for name, path in files.items()
            )
        ):
            raise ValueError("artifact bundle declaration has invalid values")
        key = (kind, family, version)
        if key in bundles:
            raise ValueError("artifact bundle selection is duplicated")
        bundles[key] = CommissionedArtifactBundle(files=files, expected_digest=digest)
    return bundles
