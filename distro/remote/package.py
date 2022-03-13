from utils import download_file

from .package import Package


class RemotePackage(Package):

    def acquire(self):
        assert self.resolved_url
        assert self.is_remote()
        return download_file(f'{self.resolved_url}/{self.get_filename()}')
