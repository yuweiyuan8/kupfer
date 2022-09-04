# kupferbootstrap

Kupfer Linux bootstrapping tool - drives pacstrap, makepkg, chroot, mkfs and fastboot, just to name a few.


## Documentation

Detailed docs for the main branch are available online at https://kupfer.gitlab.io/kupferbootstrap/

You can also build and view the docs locally:
```sh
cd docs/ && \
make && \
make serve
```

This will run a webserver on localhost:9999. Access it like `firefox http://localhost:9999/`


## Installation
Install Docker, Python 3 with the libraries from `requirements.txt` and put `bin/` into your `PATH`.
Then use `kupferbootstrap`.


## Quickstart
1. Initialize config with defaults, configure your device and flavour: `kupferbootstrap config init`
1. Build an image and packages along the way: `kupferbootstrap image build`


## Development
Put `dev` into `version.txt` to always rebuild kupferboostrap from this directory and use `kupferbootstrap` as normal.
