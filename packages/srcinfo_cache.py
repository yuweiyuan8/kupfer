from __future__ import annotations

import json
import logging
import os
import subprocess

from typing import Optional

from config import config
from constants import MAKEPKG_CMD, SRCINFO_FILE, SRCINFO_METADATA_FILE
from dataclass import DataClass
from exec.cmd import run_cmd
from utils import sha256sum

SRCINFO_CHECKSUM_FILES = ['PKGBUILD', SRCINFO_FILE]


class SrcinfoMetaFile(DataClass):

    checksums: dict[str, str]
    build_mode: Optional[str]
    src_initialised: Optional[str]

    _relative_path: str
    _changed: bool

    @staticmethod
    def parse_existing(relative_pkg_dir: str) -> SrcinfoMetaFile:
        'tries to parse the srcinfo_meta.json file in the specified pkgbuild dir'
        pkgdir = os.path.join(config.get_path('pkgbuilds'), relative_pkg_dir)
        srcinfo_meta_file = os.path.join(pkgdir, SRCINFO_METADATA_FILE)
        if not os.path.exists(srcinfo_meta_file):
            raise Exception(f"{relative_pkg_dir}: {SRCINFO_METADATA_FILE} doesn't exist")
        with open(srcinfo_meta_file, 'r') as meta_fd:
            metadata_raw = json.load(meta_fd)
        defaults = {'src_initialised': None}
        return SrcinfoMetaFile.fromDict(defaults | metadata_raw | {
            '_relative_path': relative_pkg_dir,
            '_changed': False,
        })

    @staticmethod
    def generate_new(relative_pkg_dir: str, write: bool = True) -> tuple[SrcinfoMetaFile, list[str]]:
        'Creates a new SrcinfoMetaFile object with checksums, creating a SRCINFO as necessary'
        s = SrcinfoMetaFile({
            '_relative_path': relative_pkg_dir,
            '_changed': True,
            'build_mode': '',
            'checksums': {},
            'src_initialised': None,
        })
        return s, s.refresh_all()

    @staticmethod
    def handle_directory(relative_pkg_dir: str, force_refresh: bool = False, write: bool = True) -> tuple[SrcinfoMetaFile, list[str]]:
        lines = None
        # try reading existing cache metadata
        try:
            metadata = SrcinfoMetaFile.parse_existing(relative_pkg_dir)
        except Exception as ex:
            logging.debug(f"{relative_pkg_dir}: something went wrong parsing json from {SRCINFO_METADATA_FILE},"
                          f"running `makepkg --printsrcinfo` instead instead: {ex}")
            return SrcinfoMetaFile.generate_new(relative_pkg_dir, write=write)
        # if for whatever reason only the SRCINFO got deleted but PKGBUILD has not been modified,
        # we do want the checksum verification to work. So regenerate SRCINFO first.
        if not os.path.exists(os.path.join(config.get_path('pkgbuilds'), relative_pkg_dir, SRCINFO_FILE)):
            lines = metadata.refresh_srcinfo()
        if not metadata.validate_checksums():
            # metadata is invalid
            return SrcinfoMetaFile.generate_new(relative_pkg_dir, write=write)
        # metadata is valid
        assert metadata
        if not force_refresh:
            logging.debug(f'{metadata._relative_path}: srcinfo checksums match!')
            lines = lines or metadata.read_srcinfo_file()
        else:
            lines = metadata.refresh_all(write=write)
        return metadata, lines

    def refresh_checksums(self):
        pkgdir = os.path.join(config.get_path('pkgbuilds'), self._relative_path)
        if 'checksums' not in self:
            self['checksums'] = None
        checksums_old = self.checksums.copy()
        checksums = {p: sha256sum(os.path.join(pkgdir, p)) for p in SRCINFO_CHECKSUM_FILES}
        if self.checksums is None:
            self.checksums = checksums
        else:
            self.checksums.clear()
            self.checksums.update(checksums)
        if checksums != checksums_old:
            self._changed = True

    def refresh_build_mode(self):
        self['build_mode'] = None
        with open(os.path.join(config.get_path('pkgbuilds'), self._relative_path, 'PKGBUILD'), 'r') as file:
            lines = file.read().split('\n')
        for line in lines:
            if line.startswith('_mode='):
                self.build_mode = line.split('=', 1)[1].strip("\"'")
                return

    def refresh_srcinfo(self) -> list[str]:
        'Run `makepkg --printsrcinfo` to create an updated SRCINFO file and return the lines from it'
        logging.info(f"{self._relative_path}: Generating SRCINFO with makepkg")
        pkgdir = os.path.join(config.get_path('pkgbuilds'), self._relative_path)
        srcinfo_file = os.path.join(pkgdir, SRCINFO_FILE)
        sproc = run_cmd(
            MAKEPKG_CMD + ['--printsrcinfo'],
            cwd=pkgdir,
            stdout=subprocess.PIPE,
        )
        assert (isinstance(sproc, subprocess.CompletedProcess))
        if sproc.returncode:
            raise Exception(f"{self._relative_path}: makepkg failed to parse the PKGBUILD! Error code: {sproc.returncode}")
        output = sproc.stdout.decode('utf-8')
        with open(srcinfo_file, 'w') as srcinfo_fd:
            srcinfo_fd.write(output)
        return output.split('\n')

    def read_srcinfo_file(self) -> list[str]:
        with open(os.path.join(config.get_path('pkgbuilds'), self._relative_path, SRCINFO_FILE), 'r') as srcinfo_fd:
            lines = srcinfo_fd.read().split('\n')
        return lines

    def refresh_all(self, write: bool = True) -> list[str]:
        lines = self.refresh_srcinfo()
        self.refresh_checksums()
        self.refresh_build_mode()
        if write:
            self.write()
        return lines

    def validate_checksums(self) -> bool:
        "Returns True if all checksummed files exist and checksums match"
        pkgdir = os.path.join(config.get_path('pkgbuilds'), self._relative_path)
        assert self.checksums
        for filename in SRCINFO_CHECKSUM_FILES:
            if filename not in self.checksums:
                logging.debug(f"{self._relative_path}: No checksum for {filename} available")
                return False
            checksum = self.checksums[filename]
            path = os.path.join(pkgdir, filename)
            if not os.path.exists(path):
                logging.debug(f"{self._relative_path}: can't checksum'{filename}: file doesn't exist")
                return False
            file_sum = sha256sum(path)
            if file_sum != checksum:
                logging.debug(f'{self._relative_path}: Checksum for file "{filename}" doesn\'t match')
                return False
        return True

    def toJSON(self) -> str:
        'Returns a json representation, with private keys that start with "_" filtered out'
        return json.dumps({key: val for key, val in self.toDict().items() if not key.startswith('_')}, indent=2)

    def write(self):
        'Write the filtered json representation to disk as srcinfo_meta.json'
        filepath = os.path.join(config.get_path('pkgbuilds'), self._relative_path, SRCINFO_METADATA_FILE)
        logging.debug(f'{self._relative_path}: writing {SRCINFO_METADATA_FILE}')
        with open(filepath, 'w') as fd:
            fd.write(self.toJSON())
