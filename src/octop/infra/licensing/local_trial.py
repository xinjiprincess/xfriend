"""User-scoped offline trial record, separate from server-signed entitlements.

Windows DPAPI protects the record at rest, and a registry mirror survives removing
an application data directory. This is not a server signature: an administrator or
the same user deliberately modifying the executable can bypass offline licensing.
"""

from __future__ import annotations

import ctypes
import os
import sqlite3
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_REGISTRY_PATH = r"Software\Singu\Xfriend\License"
_REGISTRY_VALUE = "LocalTrialV1"
_ENTROPY = b"singu-xfriend:local-trial:v1"


class LocalTrialRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    product: Literal["singu-xfriend"] = "singu-xfriend"
    device_id: str = Field(min_length=64, max_length=64)
    started_at: int = Field(gt=0)
    expires_at: int = Field(gt=0)
    last_seen: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_times(self) -> LocalTrialRecord:
        if self.expires_at <= self.started_at or self.last_seen < self.started_at:
            raise ValueError("Invalid local trial timestamps")
        return self


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise OSError("Offline trial storage requires Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source_buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(_ENTROPY)
    source = _Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = _Blob(len(_ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    # The description is intentionally null on both calls; no second allocation
    # is returned from CryptUnprotectData. UI_FORBIDDEN prevents interactive UI.
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_Blob),
    ]
    operation.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not operation(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(result)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


def local_trial_directory() -> Path:
    if os.name != "nt" or not os.environ.get("LOCALAPPDATA"):
        raise OSError("Offline trial storage requires a Windows user profile")
    # XFRIEND_HOME and the installation path deliberately do not affect this.
    return Path(os.environ["LOCALAPPDATA"]) / "Singu" / "Xfriend" / "License"


class LocalTrialStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory
        self.started_wall = int(time.time())
        self.started_monotonic = time.monotonic()

    def _read_mirror(self) -> bytes | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH) as key:
                value, value_type = winreg.QueryValueEx(key, _REGISTRY_VALUE)
        except FileNotFoundError:
            return None
        if value_type != winreg.REG_BINARY or not isinstance(value, bytes):
            raise ValueError("Invalid local trial registry value")
        return value

    def _write_mirror(self, value: bytes) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REGISTRY_PATH) as key:
            winreg.SetValueEx(key, _REGISTRY_VALUE, 0, winreg.REG_BINARY, value)

    def clock_floor(self) -> int:
        return self.started_wall + int(time.monotonic() - self.started_monotonic)

    def refresh(self, device_id: str, days: int, now: int) -> LocalTrialRecord:
        """Atomically claim once, or merge both existing anchors conservatively."""
        directory = self.directory if self.directory is not None else local_trial_directory()
        directory.mkdir(parents=True, exist_ok=True)
        database = directory / "trial-v1.sqlite3"
        connection = sqlite3.connect(database, timeout=5)
        try:
            # Serializes claims across processes and different XFRIEND_HOME dirs.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS trial (id INTEGER PRIMARY KEY CHECK (id = 1), "
                "record BLOB NOT NULL)"
            )
            row = connection.execute("SELECT record FROM trial WHERE id = 1").fetchone()
            mirror = self._read_mirror()
            records = []
            for blob in (row[0] if row else None, mirror):
                if blob is None:
                    continue
                if not isinstance(blob, bytes):
                    raise ValueError("Invalid local trial storage value")
                records.append(LocalTrialRecord.model_validate_json(_crypt(blob, decrypt=True)))
            if any(record.device_id != device_id for record in records):
                raise ValueError("Offline trial belongs to another machine")
            if records:
                started = min(record.started_at for record in records)
                expires = min(started + days * 86400, *(record.expires_at for record in records))
                last_seen = max(now, *(record.last_seen for record in records))
            else:
                started, expires, last_seen = now, now + days * 86400, now
            record = LocalTrialRecord(
                device_id=device_id, started_at=started, expires_at=expires, last_seen=last_seen
            )
            protected = _crypt(record.model_dump_json().encode(), decrypt=False)
            connection.execute(
                "INSERT INTO trial VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET record = excluded.record",
                (protected,),
            )
            # Write the mirror first: an interrupted SQLite commit still retains
            # the earliest start and latest observed time on the next attempt.
            self._write_mirror(protected)
            connection.commit()
            return record
        finally:
            connection.close()
