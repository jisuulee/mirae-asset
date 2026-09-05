"""Read-only, streaming access to directory and ZIP corpus sources."""

from __future__ import annotations

import logging
import os
import stat as stat_module
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import BinaryIO, Self

from .config import MANIFEST_FILENAME
from .utils import normalize_relative_posix_path

LOGGER = logging.getLogger(__name__)


def _as_posix(value: str) -> str:
    """Return a clean, NFC-normalized relative POSIX path."""

    return normalize_relative_posix_path(value)


@dataclass(frozen=True, slots=True)
class SourceFile:
    """Metadata plus a repeatable binary opener for one source file."""

    relative_path: str
    source_path: str
    size: int
    _source: "CorpusSource"
    _member: object

    def open(self) -> AbstractContextManager[BinaryIO]:
        return self._source.open_member(self._member)


@dataclass(frozen=True, slots=True)
class SourceIssue:
    stage: str
    path: str
    error: str


class CorpusSource(ABC):
    """Common interface for extracted directories and ZIP archives."""

    kind: str
    location: Path
    logical_root: str
    corpus_root_path: Path | None = None

    def _initialize_issue_tracking(self) -> None:
        self.discovery_issues: list[SourceIssue] = []
        self._discovery_issue_keys: set[tuple[str, str, str]] = set()

    def _record_issue(self, stage: str, path: object, error: object) -> None:
        key = (stage, str(path), str(error))
        if key in self._discovery_issue_keys:
            return
        self._discovery_issue_keys.add(key)
        self.discovery_issues.append(SourceIssue(*key))
        LOGGER.warning("%s failed for %s: %s", stage, path, error)

    @abstractmethod
    def iter_files(self) -> Iterator[SourceFile]:
        """Yield each file once without loading file contents eagerly."""

    @abstractmethod
    def open_member(self, member: object) -> AbstractContextManager[BinaryIO]:
        """Open a previously yielded member for binary reading."""

    def find_manifest(self) -> SourceFile | None:
        matches = [
            entry
            for entry in self.iter_files()
            if PurePosixPath(entry.relative_path).name == MANIFEST_FILENAME
        ]
        if not matches:
            return None
        if len(matches) > 1:
            paths = ", ".join(entry.source_path for entry in matches)
            raise ValueError(f"Multiple {MANIFEST_FILENAME} files found: {paths}")
        return matches[0]

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class DirectoryCorpusSource(CorpusSource):
    kind = "directory"

    def __init__(self, location: Path) -> None:
        self._initialize_issue_tracking()
        self.location = location.resolve()
        self._root = self._discover_logical_root(self.location)
        self.corpus_root_path = self._root
        self.logical_root = (
            _as_posix(str(self._root.relative_to(self.location)))
            if self._root != self.location
            else ""
        )

    def _walk_error(self, error: OSError) -> None:
        self._record_issue("directory_walk", error.filename or self.location, error)

    def _discover_logical_root(self, location: Path) -> Path:
        direct = location / MANIFEST_FILENAME
        if direct.is_file():
            return location

        matches: list[Path] = []
        for root, directories, files in os.walk(
            location, followlinks=False, onerror=self._walk_error
        ):
            directories.sort()
            files.sort()
            if MANIFEST_FILENAME in files:
                matches.append(Path(root) / MANIFEST_FILENAME)
                if len(matches) > 1:
                    break
        if len(matches) == 1:
            return matches[0].parent
        if len(matches) > 1:
            paths = ", ".join(str(path) for path in matches)
            raise ValueError(f"Multiple {MANIFEST_FILENAME} files found: {paths}")
        return location

    def iter_files(self) -> Iterator[SourceFile]:
        for root, directories, files in os.walk(
            self._root, followlinks=False, onerror=self._walk_error
        ):
            safe_directories: list[str] = []
            for directory in directories:
                directory_path = Path(root) / directory
                if directory_path.is_symlink():
                    self._record_issue(
                        "symlink_skipped", directory_path, "directory symlink"
                    )
                else:
                    safe_directories.append(directory)
            directories[:] = safe_directories
            directories.sort()
            files.sort()
            root_path = Path(root)
            for filename in files:
                path = root_path / filename
                if path.is_symlink():
                    self._record_issue("symlink_skipped", path, "file symlink")
                    continue
                try:
                    file_stat = path.stat()
                except OSError as exc:
                    self._record_issue("file_stat", path, exc)
                    continue
                if not stat_module.S_ISREG(file_stat.st_mode):
                    continue
                raw_relative = path.relative_to(self._root).as_posix()
                yield SourceFile(
                    relative_path=_as_posix(raw_relative),
                    source_path=raw_relative,
                    size=file_stat.st_size,
                    _source=self,
                    _member=path,
                )

    def open_member(self, member: object) -> AbstractContextManager[BinaryIO]:
        if not isinstance(member, Path):
            raise TypeError(f"Unexpected directory member: {type(member)!r}")
        return member.open("rb")


class ZipCorpusSource(CorpusSource):
    kind = "zip"

    def __init__(self, location: Path) -> None:
        self._initialize_issue_tracking()
        self.location = location.resolve()
        self._zip = zipfile.ZipFile(self.location, mode="r")
        try:
            self.corpus_root_path = None
            self._files = [info for info in self._zip.infolist() if not info.is_dir()]
            self._prefix = self._discover_prefix(self._files)
            self.logical_root = _as_posix(self._prefix.rstrip("/"))
        except BaseException:
            self._zip.close()
            raise

    @staticmethod
    def _discover_prefix(files: list[zipfile.ZipInfo]) -> str:
        manifests = [
            info.filename.replace("\\", "/")
            for info in files
            if PurePosixPath(info.filename.replace("\\", "/")).name
            == MANIFEST_FILENAME
        ]
        if not manifests:
            return ""
        if len(manifests) > 1:
            paths = ", ".join(manifests)
            raise ValueError(f"Multiple {MANIFEST_FILENAME} files found in ZIP: {paths}")
        parent = str(PurePosixPath(manifests[0]).parent)
        return "" if parent == "." else f"{parent}/"

    def iter_files(self) -> Iterator[SourceFile]:
        for info in self._files:
            member_path = info.filename.replace("\\", "/")
            if self._prefix and not member_path.startswith(self._prefix):
                continue
            raw_relative = member_path[len(self._prefix) :]
            if not raw_relative:
                continue
            try:
                normalized_relative = _as_posix(raw_relative)
            except ValueError as exc:
                self._record_issue("unsafe_member_path", raw_relative, exc)
                continue
            yield SourceFile(
                relative_path=normalized_relative,
                source_path=raw_relative,
                size=info.file_size,
                _source=self,
                _member=info,
            )

    def open_member(self, member: object) -> AbstractContextManager[BinaryIO]:
        if not isinstance(member, zipfile.ZipInfo):
            raise TypeError(f"Unexpected ZIP member: {type(member)!r}")
        return self._zip.open(member, mode="r")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._zip.close()


def open_corpus_source(data_root: str | Path) -> CorpusSource:
    """Open *data_root* as a directory or ZIP source."""

    location = Path(data_root).expanduser()
    if not location.exists():
        raise FileNotFoundError(f"Dataset path does not exist: {location}")
    if location.is_dir():
        return DirectoryCorpusSource(location)
    if location.is_file() and zipfile.is_zipfile(location):
        return ZipCorpusSource(location)
    raise ValueError(f"Dataset path must be a directory or ZIP archive: {location}")
