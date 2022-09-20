from __future__ import annotations

import click
import json
import logging
import os

from dataclasses import dataclass
from typing import Optional

from config import config
from constants import FLAVOUR_INFO_FILE

from .pkgbuild import discover_pkgbuilds, get_pkgbuild_by_name, init_pkgbuilds, Pkgbuild

profile_option = click.option('-p', '--profile', help="name of the profile to use", required=False, default=None)


@dataclass
class FlavourInfo:
    rootfs_size: int  # rootfs size in GB

    def __repr__(self):
        return f'rootfs_size: {self.rootfs_size}'


@dataclass
class Flavour:
    name: str
    pkgbuild: Pkgbuild
    description: str
    flavour_info: Optional[FlavourInfo]

    @staticmethod
    def from_pkgbuild(pkgbuild: Pkgbuild) -> Flavour:
        name = pkgbuild.name
        if not name.startswith('flavour-'):
            raise Exception(f'Flavour package "{name}" doesn\'t start with "flavour-": "{name}"')
        if name.endswith('-common'):
            raise Exception(f'Flavour package "{name}" ends with "-common": "{name}"')
        name = name[8:]  # split off 'flavour-'
        return Flavour(name=name, pkgbuild=pkgbuild, description=pkgbuild.description, flavour_info=None)

    def __repr__(self):
        return f'Flavour "{self.name}": "{self.description}", package: {self.pkgbuild.name if self.pkgbuild else "??? PROBABLY A BUG!"}{f", {self.flavour_info}" if self.flavour_info else ""}'

    def parse_flavourinfo(self, lazy: bool = True):
        if lazy and self.flavour_info is not None:
            return self.flavour_info
        infopath = os.path.join(config.get_path('pkgbuilds'), self.pkgbuild.path, FLAVOUR_INFO_FILE)
        if not os.path.exists(infopath):
            raise Exception(f"Error parsing flavour info for flavour {self.name}: file doesn't exist: {infopath}")
        try:
            with open(infopath, 'r') as fd:
                infodict = json.load(fd)
            i = FlavourInfo(**infodict)
        except Exception as ex:
            raise Exception(f"Error parsing {FLAVOUR_INFO_FILE} for flavour {self.name}: {ex}")
        self.flavour_info = i
        return i


_flavours_discovered: bool = False
_flavours_cache: dict[str, Flavour] = {}


def get_flavours(lazy: bool = True):
    global _flavours_cache, _flavours_discovered
    if lazy and _flavours_discovered:
        return _flavours_cache
    logging.info("Searching PKGBUILDs for flavour packages")
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
    if not flavours:
        raise Exception("No flavours found!")
    for f in flavours.values():
        print(f)
