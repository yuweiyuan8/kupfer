import logging
from typing import Iterable

from constants import Arch
from distro.abstract import DistroInfo

from .source_repo import SourceRepo, Pkgbuild


class SourceDistro(DistroInfo):
    repos: dict[str, SourceRepo]

    def build_package_levels(
        self,
        build_levels: list[set[Pkgbuild]],
        arch: Arch,
        force: bool = False,
        enable_crosscompile: bool = True,
        enable_crossdirect: bool = True,
        enable_ccache: bool = True,
        clean_chroot: bool = False,
    ):
        for level, packages in enumerate(build_levels):
            logging.info(f"(Level {level}) Building {', '.join([x.name for x in packages])}")
            for package in packages:
                package.build(
                    arch=arch,
                    enable_crosscompile=enable_crosscompile,
                    enable_crossdirect=enable_crossdirect,
                    enable_ccache=enable_ccache,
                    clean_chroot=clean_chroot,
                )

    def build_packages(
        self,
        packages: Iterable[Pkgbuild],
        arch: Arch,
        force: bool = False,
        enable_crosscompile: bool = True,
        enable_crossdirect: bool = True,
        enable_ccache: bool = True,
        clean_chroot: bool = False,
    ):
        self.build_package_levels(
            [set(packages)],
            arch=arch,
            force=force,
            enable_crosscompile=enable_crosscompile,
            enable_crossdirect=enable_crossdirect,
            enable_ccache=enable_ccache,
            clean_chroot=clean_chroot,
        )

    def build_packages_by_paths(
        self,
        paths: Iterable[str],
        arch: Arch,
        force=False,
        enable_crosscompile: bool = True,
        enable_crossdirect: bool = True,
        enable_ccache: bool = True,
        clean_chroot: bool = False,
    ):
        if isinstance(paths, str):
            paths = [paths]

        packages = self.filter_packages_by_paths(paths, allow_empty_results=False)
        return self.build_packages(
            packages,
            arch,
            force=force,
            enable_crosscompile=enable_crosscompile,
            enable_crossdirect=enable_crossdirect,
            enable_ccache=enable_ccache,
            clean_chroot=clean_chroot,
        )
