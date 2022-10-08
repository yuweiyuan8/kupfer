import pytest

import os

from copy import copy

from config.state import ConfigStateHolder, config
from packages.pkgbuild import init_pkgbuilds, discover_pkgbuilds, Pkgbuild, parse_pkgbuild
from .device import Device, DEVICE_DEPRECATIONS, get_device, get_devices, parse_device_pkg, check_devicepkg_name


@pytest.fixture(scope='session')
def initialise_pkgbuilds_dir() -> ConfigStateHolder:
    config.try_load_file()
    init_pkgbuilds(interactive=False)
    return config


@pytest.fixture()
def pkgbuilds_dir(initialise_pkgbuilds_dir: ConfigStateHolder) -> str:
    global config
    config = initialise_pkgbuilds_dir
    return config.get_path('pkgbuilds')


@pytest.fixture(scope='session')
def pkgbuilds_repo_cached(initialise_pkgbuilds_dir) -> dict[str, Pkgbuild]:
    return discover_pkgbuilds()


@pytest.fixture()
def pkgbuilds_repo(pkgbuilds_dir, pkgbuilds_repo_cached):
    # use pkgbuilds_dir to ensure global config gets overriden, can't be done from session scope fixtures
    return pkgbuilds_repo_cached


ONEPLUS_ENCHILADA = 'sdm845-oneplus-enchilada'
ONEPLUS_ENCHILADA_PKG = f'device-{ONEPLUS_ENCHILADA}'


@pytest.fixture(scope='session')
def enchilada_pkgbuild(initialise_pkgbuilds_dir: ConfigStateHolder):
    config = initialise_pkgbuilds_dir
    config.try_load_file()
    return parse_pkgbuild(os.path.join('device', ONEPLUS_ENCHILADA_PKG), _config=config)[0]


def validate_oneplus_enchilada(d: Device):
    assert d
    assert d.arch == 'aarch64'
    assert d.package and d.package.name == ONEPLUS_ENCHILADA_PKG


def test_fixture_initialise_pkgbuilds_dir(initialise_pkgbuilds_dir: ConfigStateHolder):
    assert os.path.exists(os.path.join(config.get_path('pkgbuilds'), 'device'))


def test_fixture_pkgbuilds_dir(pkgbuilds_dir):
    assert os.path.exists(os.path.join(pkgbuilds_dir, 'device'))


def test_get_device():
    name = ONEPLUS_ENCHILADA
    d = get_device(name)
    validate_oneplus_enchilada(d)


def test_get_device_deprecated():
    name = 'oneplus-enchilada'
    assert name in DEVICE_DEPRECATIONS
    d = get_device(name)
    # currently redirects to correct package, need to change this test when changed to an exception
    validate_oneplus_enchilada(d)


def test_parse_device_pkg_enchilada(enchilada_pkgbuild):
    validate_oneplus_enchilada(parse_device_pkg(enchilada_pkgbuild))


def test_parse_device_pkg_malformed_arch(enchilada_pkgbuild):
    enchilada_pkgbuild = copy(enchilada_pkgbuild)
    enchilada_pkgbuild.arches.append('x86_64')
    with pytest.raises(Exception):
        parse_device_pkg(enchilada_pkgbuild)


def test_discover_packages_and_warm_cache_sorry_takes_long(pkgbuilds_repo):
    # mostly used to warm up the cache in a user-visible way
    assert pkgbuilds_repo
    assert ONEPLUS_ENCHILADA_PKG in pkgbuilds_repo


def test_get_devices(pkgbuilds_repo: dict[str, Pkgbuild]):
    d = get_devices(pkgbuilds_repo)
    assert d
    assert ONEPLUS_ENCHILADA in d
    for p in d.values():
        check_devicepkg_name(p.package.name)
    assert 'sdm845-oneplus-common' not in d
    validate_oneplus_enchilada(d[ONEPLUS_ENCHILADA])
