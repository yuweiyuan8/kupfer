import click

from .forwarding import cmd_forwarding
from .ssh import cmd_ssh
from .telnet import cmd_telnet

cmd_net = click.Group('net', help='Network utilities like ssh and telnet')
for cmd in cmd_forwarding, cmd_ssh, cmd_telnet:
    cmd_net.add_command(cmd)
