from __future__ import annotations

from dataclasses import dataclass
from munch import Munch
from typing import ClassVar, Optional, Union, Mapping, Any, get_type_hints, get_origin, get_args, Iterable
from types import UnionType


def munchclass(*args, init=False, **kwargs):
    return dataclass(*args, init=init, slots=True, **kwargs)


def resolve_type_hint(hint: type) -> Iterable[type]:
    origin = get_origin(hint)
    args: Iterable[type] = get_args(hint)
    if origin is Optional:
        args = set(list(args) + [type(None)])
    if origin in [Union, UnionType, Optional]:
        results: list[type] = []
        for arg in args:
            results += resolve_type_hint(arg)
        return results
    return [origin or hint]


class DataClass(Munch):

    _type_hints: ClassVar[dict[str, Any]]

    def __init__(self, d: dict = {}, validate: bool = True, **kwargs):
        self.update(d | kwargs, validate=validate)

    @classmethod
    def transform(cls, values: Mapping[str, Any], validate: bool = True, allow_extra: bool = False) -> Any:
        results = {}
        values = dict(values)
        for key in list(values.keys()):
            value = values.pop(key)
            type_hints = cls._type_hints
            if key in type_hints:
                _classes = tuple[type](resolve_type_hint(type_hints[key]))
                optional = type(None) in _classes
                if issubclass(_classes[0], dict):
                    assert isinstance(value, dict) or optional
                    target_class = _classes[0]
                    if target_class is dict:
                        target_class = Munch
                    if not isinstance(value, target_class):
                        if not (optional and value is None):
                            assert issubclass(target_class, Munch)
                            # despite the above assert, mypy doesn't seem to understand target_class is a Munch here
                            kwargs = {'validate': validate} if issubclass(target_class, DataClass) else {}
                            value = target_class.fromDict(value, **kwargs)  # type:ignore[attr-defined]
                # handle numerics
                elif set(_classes).intersection([int, float]) and isinstance(value, str) and str not in _classes:
                    parsed_number = None
                    parsers: list[tuple[type, list]] = [(int, [10]), (int, [0]), (float, [])]
                    for _cls, args in parsers:
                        if _cls not in _classes:
                            continue
                        try:
                            parsed_number = _cls(value, *args)
                            break
                        except ValueError:
                            continue
                    if parsed_number is None:
                        if validate:
                            raise Exception(f"Couldn't parse string value {repr(value)} for key '{key}' into number formats: " +
                                            (', '.join(list(c.__name__ for c in _classes))))
                    else:
                        value = parsed_number
                if validate:
                    if not isinstance(value, _classes):
                        raise Exception(f'key "{key}" has value of wrong type! expected: '
                                        f'{" ,".join([ c.__name__ for c in _classes])}; '
                                        f'got: {type(value).__name__}; value: {value}')
            elif validate and not allow_extra:
                raise Exception(f'Unknown key "{key}"')
            else:
                if isinstance(value, dict) and not isinstance(value, Munch):
                    value = Munch.fromDict(value)
            results[key] = value
        if values:
            if validate:
                raise Exception(f'values contained unknown keys: {list(values.keys())}')
            results |= values

        return results

    @classmethod
    def fromDict(cls, values: Mapping[str, Any], validate: bool = True):
        return cls(**cls.transform(values, validate))

    def update(self, d: Mapping[str, Any], validate: bool = True):
        Munch.update(self, type(self).transform(d, validate))

    def __init_subclass__(cls):
        super().__init_subclass__()
        cls._type_hints = {name: hint for name, hint in get_type_hints(cls).items() if get_origin(hint) is not ClassVar}

    def __repr__(self):
        return f'{type(self)}{dict.__repr__(self.toDict())}'
