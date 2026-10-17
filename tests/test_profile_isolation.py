from __future__ import annotations

from re_gpt.profile_isolation import ProfileDirectoryLease, canonical_profile_dir


def test_canonical_profile_paths_are_distinct_per_profile(tmp_path) -> None:
    a = canonical_profile_dir(tmp_path / "account-a")
    b = canonical_profile_dir(tmp_path / "account-b")
    assert a != b
    assert a.is_absolute()
    assert b.is_absolute()


def test_profile_leases_are_path_local(tmp_path) -> None:
    a = ProfileDirectoryLease(tmp_path / "account-a", timeout_seconds=0.0)
    b = ProfileDirectoryLease(tmp_path / "account-b", timeout_seconds=0.0)
    a.acquire()
    try:
        b.acquire()
        try:
            assert a.lease_path != b.lease_path
            assert a.lease_path.parent != b.lease_path.parent
        finally:
            b.release()
    finally:
        a.release()


def test_same_profile_cannot_be_double_leased(tmp_path) -> None:
    first = ProfileDirectoryLease(tmp_path / "same", timeout_seconds=0.0)
    second = ProfileDirectoryLease(tmp_path / "same", timeout_seconds=0.0)
    first.acquire()
    try:
        try:
            second.acquire()
        except RuntimeError as exc:
            assert "profile is already in use" in str(exc).lower()
        else:
            raise AssertionError("same profile lease unexpectedly acquired twice")
    finally:
        first.release()
