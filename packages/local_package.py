from .pkgbuild import Pkgbuild

from distro.package import PackageInfo


class LocalPackage(PackageInfo):
    pkgbuild: Pkgbuild

    def __init__(self, source_pkgbuild: Pkgbuild):
        self.pkgbuild = source_pkgbuild
