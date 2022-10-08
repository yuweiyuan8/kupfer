import click

from .device import get_devices


@click.command(name='devices')
def cmd_devices():
    'list the available devices and descriptions'
    devices = get_devices()
    if not devices:
        raise Exception("No devices found!")
    for d in sorted(devices.keys()):
        print(devices[d])
