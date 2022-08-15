import click
import logging

from exec import run_root_cmd
from ssh import run_ssh_command
from wrapper import check_programs_wrap


@click.command(name='forwarding')
def cmd_forwarding():
    """Enable network forwarding for a usb-attached device"""
    check_programs_wrap(['syctl', 'iptables'])

    logging.info("Enabling ipv4 forwarding with sysctl")
    result = run_root_cmd([
        'sysctl',
        'net.ipv4.ip_forward=1',
    ])
    if result.returncode != 0:
        click.Abort('Failed to enable ipv4 forward via sysctl')

    logging.info("Enabling ipv4 forwarding with iptables")
    result = run_root_cmd([
        'iptables',
        '-P',
        'FORWARD',
        'ACCEPT',
    ])
    if result.returncode != 0:
        click.Abort('Failed set iptables rule')

    logging.info("Enabling ipv4 NATting with iptables")
    result = run_root_cmd([
        'iptables',
        '-A',
        'POSTROUTING',
        '-t',
        'nat',
        '-j',
        'MASQUERADE',
        '-s',
        '172.16.42.0/24',
    ])
    if result.returncode != 0:
        click.Abort('Failed set iptables rule')

    logging.info("Setting default route on device via ssh")
    result = run_ssh_command(cmd=['sudo -S route add default gw 172.16.42.2'])
    if result.returncode != 0:
        click.Abort('Failed to add gateway over ssh')
