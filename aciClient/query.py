"""Structured APIC query parameters and filter expressions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .exceptions import QueryParameterError

type QueryValue = str | int | float | bool | Sequence[str]


@dataclass(frozen=True)
class Filter:
    expression: str

    def __str__(self):
        return self.expression


def _quote_value(value: Any) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _property_filter(name: str, attribute: str, *values: Any) -> Filter:
    arguments = ",".join((attribute, *map(_quote_value, values)))
    return Filter(f"{name}({arguments})")


def eq(attribute: str, value: Any) -> Filter:
    return _property_filter("eq", attribute, value)


def ne(attribute: str, value: Any) -> Filter:
    return _property_filter("ne", attribute, value)


def lt(attribute: str, value: Any) -> Filter:
    return _property_filter("lt", attribute, value)


def le(attribute: str, value: Any) -> Filter:
    return _property_filter("le", attribute, value)


def gt(attribute: str, value: Any) -> Filter:
    return _property_filter("gt", attribute, value)


def ge(attribute: str, value: Any) -> Filter:
    return _property_filter("ge", attribute, value)


def between(attribute: str, lower: Any, upper: Any) -> Filter:
    return _property_filter("bw", attribute, lower, upper)


def any_bit(attribute: str, value: Any) -> Filter:
    return _property_filter("anybit", attribute, value)


def all_bits(attribute: str, value: Any) -> Filter:
    return _property_filter("allbits", attribute, value)


def begins_with(attribute: str, value: Any) -> Filter:
    return _property_filter("wcard", attribute, f"^{value}")


def wildcard(attribute: str, value: Any) -> Filter:
    return _property_filter("wcard", attribute, value)


def and_(*filters: Filter) -> Filter:
    return Filter("and(" + ",".join(map(str, filters)) + ")")


def or_(*filters: Filter) -> Filter:
    return Filter("or(" + ",".join(map(str, filters)) + ")")


def xor_(*filters: Filter) -> Filter:
    return Filter("xor(" + ",".join(map(str, filters)) + ")")


def not_(expression: Filter) -> Filter:
    return Filter(f"not({expression})")


def always_true() -> Filter:
    return Filter("true()")


def always_false() -> Filter:
    return Filter("false()")


def property_holder(attribute: str) -> Filter:
    return Filter(f"pholder({attribute})")


def passive_holder(value: Any) -> Filter:
    return Filter(f"passive({_quote_value(value)})")


def raw_filter(expression: str) -> Filter:
    return Filter(expression)


class QueryParams(tuple):
    def __new__(
        cls,
        value: Mapping[str, QueryValue]
        | Sequence[tuple[str, QueryValue]]
        | None = None,
    ):
        pairs = value.items() if isinstance(value, Mapping) else (value or ())
        expanded: list[tuple[str, str]] = []
        for key, item in pairs:
            values = item if isinstance(item, (list, tuple)) else (item,)
            expanded.extend(
                (str(key), "true" if v is True else "false" if v is False else str(v))
                for v in values
            )
        return super().__new__(cls, expanded)

    def replace(self, **values: Any) -> QueryParams:
        names = {k.replace("_", "-") for k in values}
        pairs = [(k, v) for k, v in self if k not in names]
        pairs.extend(
            (k.replace("_", "-"), v) for k, v in values.items() if v is not None
        )
        return QueryParams(pairs)


@dataclass(frozen=True)
class AciQuery:
    query_target: str | None = None
    target_subtree_class: tuple[str, ...] | None = None
    query_target_filter: Filter | str | None = None
    rsp_subtree: str | None = None
    rsp_subtree_class: tuple[str, ...] | None = None
    rsp_subtree_filter: Filter | str | None = None
    rsp_subtree_include: str | None = None
    order_by: str | None = None
    page: int | None = None
    page_size: int | None = None
    time_range: str | None = None
    subscription: bool | None = None
    refresh_timeout: int | None = None

    def pairs(self) -> QueryParams:
        mapping = {
            "query-target": self.query_target,
            "target-subtree-class": self.target_subtree_class,
            "query-target-filter": self.query_target_filter,
            "rsp-subtree": self.rsp_subtree,
            "rsp-subtree-class": self.rsp_subtree_class,
            "rsp-subtree-filter": self.rsp_subtree_filter,
            "rsp-subtree-include": self.rsp_subtree_include,
            "order-by": self.order_by,
            "page": self.page,
            "page-size": self.page_size,
            "time-range": self.time_range,
            "subscription": self.subscription,
            "refresh-timeout": self.refresh_timeout,
        }
        pairs = []
        for key, value in mapping.items():
            if value is None:
                continue
            if isinstance(value, tuple):
                value = ",".join(value)
            pairs.append(
                (
                    key,
                    str(value)
                    if not isinstance(value, bool)
                    else ("yes" if value else "no"),
                )
            )
        return QueryParams(pairs)


_SINGLETONS = {
    "page",
    "page-size",
    "query-target",
    "query-target-filter",
    "rsp-subtree",
    "rsp-subtree-filter",
    "subscription",
    "refresh-timeout",
}


def normalize_params(
    params: AciQuery
    | Mapping[str, QueryValue]
    | Sequence[tuple[str, QueryValue]]
    | QueryParams
    | None,
) -> QueryParams:
    result = params.pairs() if isinstance(params, AciQuery) else QueryParams(params)
    seen = set()
    for key, _ in result:
        if key in _SINGLETONS and key in seen:
            raise QueryParameterError(f"duplicate singleton query parameter: {key}")
        seen.add(key)
    return result
