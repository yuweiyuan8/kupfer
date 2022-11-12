from __future__ import annotations

from munch import Munch
from typing import Any, Optional, Mapping, Union

from dataclass import DataClass, munchclass
from constants import Arch


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
    ccache: str
    rust: str


class ProfilesSection(DataClass):
    current: str
    default: SparseProfile

    @classmethod
    def transform(cls, values: Mapping[str, Any], validate: bool = True, allow_extra: bool = True):
        results = {}
        for k, v in values.items():
            if k == 'current':
                results[k] = v
                continue
            if not allow_extra and k != 'default':
                raise Exception(f'Unknown key {k} in profiles section (Hint: extra_keys not allowed for some reason)')
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
    def fromDict(
        cls,
        values: Mapping[str, Any],
        validate: bool = True,
        allow_extra: bool = False,
        allow_incomplete: bool = False,
    ):
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

        return Config(_vals, validate=validate)


@munchclass()
class RuntimeConfiguration(DataClass):
    verbose: bool
    no_wrap: bool
    error_shell: bool
    config_file: Optional[str]
    script_source_dir: Optional[str]
    arch: Optional[Arch]
    uid: Optional[int]
    progress_bars: Optional[bool]


class ConfigLoadState(DataClass):
    load_finished: bool
    exception: Optional[Exception]

    def __init__(self, d: dict = {}):
        self.load_finished = False
        self.exception = None
        self.update(d)
