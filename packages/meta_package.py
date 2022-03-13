from typing import Optional

from distro.package import Package, PackageInfo
from distro.version import compare_package_versions, VerComp

from .source_repo import SourceRepo, SourcePackage
from .local_package import LocalPackage
from distro.remote.package import RemotePackage
from .pkgbuild import Pkgbuild


class MetaPackage(PackageInfo):
    pkgbuild: Optional[Pkgbuild]
    local_package: Optional[LocalPackage]
    remote_package: Optional[RemotePackage]

    def __init__(self, source_pkgbuild: Optional[Pkgbuild], local_package: Optional[PackageInfo], remote_package: Optional[PackageInfo]):
        self.pkgbuild = source_pkgbuild
        self.local_package = local_package
        self.remote_package = remote_package

    def acquire(self, download=True, build=True) -> str:

        version_comparison = self.binary_package.compare_version(Pkgbuild.version)
