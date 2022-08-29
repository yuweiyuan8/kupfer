FROM archlinux:base-devel

RUN pacman-key --init && \
    pacman -Sy --noconfirm archlinux-keyring && \
    pacman -Su --noconfirm --needed \
    python python-pip \
    arch-install-scripts rsync \
    aarch64-linux-gnu-gcc aarch64-linux-gnu-binutils aarch64-linux-gnu-glibc aarch64-linux-gnu-linux-api-headers \
    git sudo \
    android-tools openssh inetutils \
    parted

RUN sed -i "s/EUID == 0/EUID == -1/g" $(which makepkg)

RUN yes | pacman -Scc

RUN sed -i "s/SigLevel.*/SigLevel = Never/g" /etc/pacman.conf

ENV KUPFERBOOTSTRAP_WRAPPED=DOCKER
ENV PATH=/app/bin:/app/local/bin:$PATH
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN python -c "from distro import distro; distro.get_kupfer_local(arch=None,in_chroot=False).repos_config_snippet()" | tee -a /etc/pacman.conf
RUN useradd -m -g users kupfer
RUN echo "kupfer ALL=(ALL) NOPASSWD: ALL" | tee /etc/sudoers.d/kupfer

WORKDIR /
