from distro.package import PackageInfo
from distro.version import compare_package_versions, VerComp

from .source_repo import SourceRepo, SourcePackage


class HybridPackage(PackageInfo):
    pkgbuild: Pkgbuild
    binary_package: PackageInfo

    def __init__(self, source_pkgbuild: Pkgbuild, binary_package: PackageInfo):
        self.pkgbuild = source_pkgbuild
        self.binary_package = binary_package

    def acquire(self, download=True, build=True) -> str:

        version_comparison = self.binary_package.compare_version(Pkgbuild.version)
