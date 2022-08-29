from dataclasses import dataclass
from munch import Munch
from typing import Optional, Union, Mapping, Any, get_type_hints, get_origin, get_args, Iterable


def munchclass(*args, init=False, **kwargs):
    return dataclass(*args, init=init, slots=True, **kwargs)


def resolve_type_hint(hint: type):
    origin = get_origin(hint)
    args: Iterable[type] = get_args(hint)
    if origin is Optional:
        args = set(list(args) + [type(None)])
    if origin in [Union, Optional]:
        results = []
        for arg in args:
            results += resolve_type_hint(arg)
        return results
    return [origin or hint]


class DataClass(Munch):

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
                _classes = tuple(resolve_type_hint(type_hints[key]))
                if issubclass(_classes[0], dict):
                    assert isinstance(value, dict)
                    target_class = _classes[0]
                    if not issubclass(_classes[0], Munch):
                        target_class = DataClass
                    if not isinstance(value, target_class):
                        value = target_class.fromDict(value, validate=validate)
                if validate:
                    if not isinstance(value, _classes):
                        raise Exception(f'key "{key}" has value of wrong type {_classes}: {value}')
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
        cls._type_hints = get_type_hints(cls)

    def __repr__(self):
        return f'{type(self)}{dict.__repr__(self.toDict())}'
