import os
from typing import Optional

from config import config
from distro.package import RemotePackage
from .pkgbuild import SourcePackage

#from .pkgbuild import Pkgbuild


class LocalPackage(RemotePackage):
    source_package: SourcePackage
    remote_package: RemotePackage
    local_package: RemotePackage

    def __init__(self, source_package: SourcePackage, remote_package: Optional[RemotePackage], local_package: Optional[RemotePackage]):
        self.source_package = source_package
        self.remote_package = remote_package

    def acquire(self) -> Optional[str]:
        file_name = self.get_filename()
        assert file_name
        file_path = os.path.join(config.get_package_dir(self.arch), self.repo_name, file_name)
        if os.path.exists(file_path):
            return file_path
        # not found: invalidate version
        self.version = None
        return None
