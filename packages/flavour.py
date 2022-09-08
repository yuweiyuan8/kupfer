from __future__ import annotations

import click
import logging

from dataclasses import dataclass
from typing import Optional

from config import config

from .pkgbuild import discover_pkgbuilds, get_pkgbuild_by_name, init_pkgbuilds, Pkgbuild


@dataclass
class Flavour:
    name: str
    pkgbuild: Pkgbuild
    description: str

    @staticmethod
    def from_pkgbuild(pkgbuild: Pkgbuild) -> Flavour:
        name = pkgbuild.name
        if not name.startswith('flavour-'):
            raise Exception(f'Flavour package "{name}" doesn\'t start with "flavour-": "{name}"')
        if name.endswith('-common'):
            raise Exception(f'Flavour package "{name}" ends with "-common": "{name}"')
        name = name[8:]  # split off 'flavour-'
        return Flavour(name=name, pkgbuild=pkgbuild, description=pkgbuild.description)

    def __repr__(self):
        return f'Flavour "{self.name}": "{self.description}", package: {self.pkgbuild.name if self.pkgbuild else "??? PROBABLY A BUG!"}'


_flavours_discovered: bool = False
_flavours_cache: dict[str, Flavour] = {}


def get_flavours(lazy: bool = True):
    global _flavours_cache, _flavours_discovered
    if lazy and _flavours_discovered:
        return _flavours_cache
    flavours: dict[str, Flavour] = {}
    pkgbuilds: dict[str, Pkgbuild] = discover_pkgbuilds(lazy=(lazy or not _flavours_discovered))
    for pkg in pkgbuilds.values():
        name = pkg.name
        if not name.startswith('flavour-') or name.endswith('-common'):
            continue
        name = name[8:]  # split off 'flavour-'
        logging.info(f"Found flavour package {name}")
        flavours[name] = Flavour.from_pkgbuild(pkg)
    _flavours_cache.clear()
    _flavours_cache.update(flavours)
    _flavours_discovered = True
    return flavours


def get_flavour(name: str, lazy: bool = True):
    global _flavours_cache
    pkg_name = f'flavour-{name}'
    if lazy and name in _flavours_cache:
        return _flavours_cache[name]
    try:
        logging.info(f"Trying to find PKGBUILD for flavour {name}")
        init_pkgbuilds()
        pkg = get_pkgbuild_by_name(pkg_name)
    except Exception as ex:
        raise Exception(f"Error parsing PKGBUILD for flavour package {pkg_name}:\n{ex}")
    assert pkg and pkg.name == pkg_name
    flavour = Flavour.from_pkgbuild(pkg)
    _flavours_cache[name] = flavour
    return flavour


def get_profile_flavour(profile_name: Optional[str] = None) -> Flavour:
    profile = config.enforce_profile_flavour_set(profile_name=profile_name)
    return get_flavour(profile.flavour)


@click.command(name='list')
def cmd_flavours_list():
    'list information about available flavours'
    flavours = get_flavours()
    for f in flavours.values():
        print(f)
