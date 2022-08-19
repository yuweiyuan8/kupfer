import logging
import os

from typing import Optional

from config import config
from constants import Arch, ARCHES
from config.scheme import DataClass, munchclass
from .pkgbuild import discover_pkgbuilds, _pkgbuilds_cache, Pkgbuild, parse_pkgbuild

DEVICE_DEPRECATIONS = {
    "oneplus-enchilada": "sdm845-oneplus-enchilada",
    "oneplus-fajita": "sdm845-oneplus-fajita",
    "xiaomi-beryllium-ebbg": "sdm845-sdm845-xiaomi-beryllium-ebbg",
    "xiaomi-beryllium-tianma": "sdm845-sdm845-xiaomi-tianma",
    "bq-paella": "msm8916-bq-paella",
}


@munchclass()
class Device(DataClass):
    name: str
    arch: Arch
    package: Pkgbuild

    def parse_deviceinfo(self):
        pass


def check_devicepkg_name(name: str, log_level: Optional[int] = None):
    valid = True
    if not name.startswith('device-'):
        valid = False
        if log_level is not None:
            logging.log(log_level, f'invalid device package name "{name}": doesn\'t start with "device-"')
    if name.endswith('-common'):
        valid = False
        if log_level is not None:
            logging.log(log_level, f'invalid device package name "{name}": ends with "-common"')
    return valid


def parse_device_pkg(pkgbuild: Pkgbuild) -> Device:
    if len(pkgbuild.arches) != 1:
        raise Exception(f"{pkgbuild.name}: Device package must have exactly one arch, but has {pkgbuild.arches}")
    arch = pkgbuild.arches[0]
    if arch == 'any' or arch not in ARCHES:
        raise Exception(f'unknown arch for device package: {arch}')
    if pkgbuild.repo != 'device':
        logging.warning(f'device package {pkgbuild.name} is in unexpected repo "{pkgbuild.repo}", expected "device"')
    name = pkgbuild.name
    prefix = 'device-'
    if name.startswith(prefix):
        name = name[len(prefix):]
    return Device(name=name, arch=arch, package=pkgbuild)


_device_cache: dict[str, Device] = {}
_device_cache_populated: bool = False


def get_devices(pkgbuilds: Optional[dict[str, Pkgbuild]] = None, lazy: bool = True) -> dict[str, Device]:
    global _device_cache, _device_cache_populated
    use_cache = _device_cache_populated and lazy
    if not use_cache:
        if not pkgbuilds:
            pkgbuilds = discover_pkgbuilds(lazy=lazy)
        _device_cache.clear()
        for pkgbuild in pkgbuilds.values():
            if not (pkgbuild.repo == 'device' and check_devicepkg_name(pkgbuild.name, log_level=None)):
                continue
            dev = parse_device_pkg(pkgbuild)
            _device_cache[dev.name] = dev
        _device_cache_populated = True
    return _device_cache.copy()


def get_device(name: str, pkgbuilds: Optional[dict[str, Pkgbuild]] = None, lazy: bool = True, scan_all=False) -> Device:
    global _device_cache, _device_cache_populated
    assert lazy or pkgbuilds
    if name in DEVICE_DEPRECATIONS:
        warning = f"Deprecated device {name}"
        replacement = DEVICE_DEPRECATIONS[name]
        if replacement:
            warning += (f': Device has been renamed to {replacement}! Please adjust your profile config!\n'
                        'This will become an error in a future version!')
            name = replacement
        logging.warning(warning)
    if lazy and name in _device_cache:
        return _device_cache[name]
    if scan_all:
        devices = get_devices(pkgbuilds=pkgbuilds, lazy=lazy)
        if name not in devices:
            raise Exception(f'Unknown device {name}!')
        return devices[name]
    else:
        pkgname = f'device-{name}'
        if pkgbuilds:
            if pkgname not in pkgbuilds:
                raise Exception(f'Unknown device {name}!')
            pkgbuild = pkgbuilds[pkgname]
        else:
            if lazy and pkgname in _pkgbuilds_cache:
                pkgbuild = _pkgbuilds_cache[pkgname]
            else:
                relative_path = os.path.join('device', pkgname)
                assert os.path.exists(os.path.join(config.get_path('pkgbuilds'), relative_path))
                pkgbuild = [p for p in parse_pkgbuild(relative_path, _config=config) if p.name == pkgname][0]
                _pkgbuilds_cache[pkgname] = pkgbuild
        device = parse_device_pkg(pkgbuild)
        if lazy:
            _device_cache[name] = device
        return device


def get_profile_device(profile_name: Optional[str] = None, hint_or_set_arch: bool = False):
    profile = config.enforce_profile_device_set(profile_name, hint_or_set_arch=hint_or_set_arch)
    return get_device(profile.device)
