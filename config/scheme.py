from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union, Mapping, Any, get_type_hints, get_origin, get_args, Iterable
from munch import Munch

from constants import Arch


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
    def transform(cls, values: Mapping[str, Any], validate: bool = True) -> Any:
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
            elif validate:
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


@munchclass()
class SparseProfile(DataClass):
    parent: Optional[str]
    device: Optional[str]
    flavour: Optional[str]
    pkgs_include: Optional[list[str]]
    pkgs_exclude: Optional[list[str]]
    hostname: Optional[str]
    username: Optional[str]
    password: Optional[str]
    size_extra_mb: Optional[Union[str, int]]

    def __repr__(self):
        return f'{type(self)}{dict.__repr__(self.toDict())}'


@munchclass()
class Profile(SparseProfile):
    parent: Optional[str]
    device: str
    flavour: str
    pkgs_include: list[str]
    pkgs_exclude: list[str]
    hostname: str
    username: str
    password: Optional[str]
    size_extra_mb: Union[str, int]


@munchclass()
class WrapperSection(DataClass):
    type: str  # NOTE: rename to 'wrapper_type' if this causes problems


@munchclass()
class BuildSection(DataClass):
    ccache: bool
    clean_mode: bool
    crosscompile: bool
    crossdirect: bool
    threads: int


@munchclass()
class PkgbuildsSection(DataClass):
    git_repo: str
    git_branch: str


@munchclass()
class PacmanSection(DataClass):
    parallel_downloads: int
    check_space: bool
    repo_branch: str


@munchclass()
class PathsSection(DataClass):
    cache_dir: str
    chroots: str
    pacman: str
    packages: str
    pkgbuilds: str
    jumpdrive: str
    images: str


class ProfilesSection(DataClass):
    current: str
    default: SparseProfile

    @classmethod
    def transform(cls, values: Mapping[str, Any], validate: bool = True):
        results = {}
        for k, v in values.items():
            if k == 'current':
                results[k] = v
                continue
            if not isinstance(v, dict):
                raise Exception(f'profile {v} is not a dict!')
            results[k] = SparseProfile.fromDict(v, validate=True)
        return results

    def update(self, d, validate: bool = True):
        Munch.update(self, self.transform(values=d, validate=validate))

    def __repr__(self):
        return f'{type(self)}{dict.__repr__(self.toDict())}'


@munchclass()
class Config(DataClass):
    wrapper: WrapperSection
    build: BuildSection
    pkgbuilds: PkgbuildsSection
    pacman: PacmanSection
    paths: PathsSection
    profiles: ProfilesSection

    @classmethod
    def fromDict(cls, values: Mapping[str, Any], validate: bool = True, allow_incomplete: bool = False):
        values = dict(values)  # copy for later modification
        _vals = {}
        for name, _class in cls._type_hints.items():
            if name not in values:
                if not allow_incomplete:
                    raise Exception(f'Config key "{name}" not in input dictionary')
                continue
            value = values.pop(name)
            if not isinstance(value, _class):
                value = _class.fromDict(value, validate=validate)
            _vals[name] = value

        if values:
            if validate:
                raise Exception(f'values contained unknown keys: {list(values.keys())}')
            _vals |= values

        return Config(**_vals, validate=validate)


@munchclass()
class RuntimeConfiguration(DataClass):
    verbose: bool
    no_wrap: bool
    error_shell: bool
    config_file: Optional[str]
    script_source_dir: Optional[str]
    arch: Optional[Arch]
    uid: Optional[int]


class ConfigLoadState(DataClass):
    load_finished: bool
    exception: Optional[Exception]

    def __init__(self, d: dict = {}):
        self.load_finished = False
        self.exception = None
        self.update(d)
