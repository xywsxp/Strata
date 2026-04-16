"""Tests for strata.core.errors — exception hierarchy integrity."""

from __future__ import annotations

import inspect
from collections.abc import Sequence

from strata import StrataError
from strata.core import errors as err_mod

_SKIP = frozenset({StrataError, Exception, BaseException, object})


def _all_exception_classes() -> Sequence[type[StrataError]]:
    result: list[type[StrataError]] = []
    for _name, obj in inspect.getmembers(err_mod, inspect.isclass):
        if issubclass(obj, StrataError) and obj is not StrataError:
            result.append(obj)
    return result


class TestAllExceptionsInheritStrataError:
    def test_all_exceptions_inherit_strata_error(self) -> None:
        for cls in _all_exception_classes():
            assert issubclass(cls, StrataError), f"{cls.__name__} must inherit StrataError"

    def test_at_least_ten_exception_classes(self) -> None:
        assert len(_all_exception_classes()) >= 10


class TestExceptionHierarchyNoCrossPackage:
    """Verify no exception class inherits from a base outside its logical package."""

    def test_harness_subtypes_only_inherit_harness(self) -> None:
        for cls in _all_exception_classes():
            if issubclass(cls, err_mod.HarnessError) and cls is not err_mod.HarnessError:
                mro_bases = {
                    b
                    for b in cls.__mro__
                    if b not in (_SKIP | {cls, err_mod.HarnessError}) and issubclass(b, StrataError)
                }
                for b in mro_bases:
                    assert issubclass(b, err_mod.HarnessError), (
                        f"{cls.__name__} has cross-package base {b.__name__}"
                    )

    def test_grounding_subtypes_only_inherit_grounding(self) -> None:
        for cls in _all_exception_classes():
            if issubclass(cls, err_mod.GroundingError) and cls is not err_mod.GroundingError:
                mro_bases = {
                    b
                    for b in cls.__mro__
                    if b not in (_SKIP | {cls, err_mod.GroundingError})
                    and issubclass(b, StrataError)
                }
                for b in mro_bases:
                    assert issubclass(b, err_mod.GroundingError), (
                        f"{cls.__name__} has cross-package base {b.__name__}"
                    )

    def test_environment_error_in_strata_namespace(self) -> None:
        builtin_env_err: type[Exception] = OSError
        assert err_mod.EnvironmentError is not builtin_env_err

    def test_silence_timeout_is_command_timeout_subclass(self) -> None:
        assert issubclass(err_mod.SilenceTimeoutError, err_mod.CommandTimeoutError)
