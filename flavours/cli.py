import click

from .flavour import get_flavours

profile_option = click.option('-p', '--profile', help="name of the profile to use", required=False, default=None)


@click.command(name='flavours')
def cmd_flavours():
    'list information about available flavours'
    flavours = get_flavours()
    if not flavours:
        raise Exception("No flavours found!")
    for name in sorted(flavours.keys()):
        f = flavours[name]
        try:
            f.parse_flavourinfo()
        except:
            pass
        print(f)
