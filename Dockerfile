FROM ubuntu:24.04

ARG S6_OVERLAY_VERSION=3.2.0.0
ARG TARGETARCH=amd64

ENV DEBIAN_FRONTEND=noninteractive \
    S6_KILL_GRACETIME=30000 \
    S6_SERVICES_GRACETIME=30000 \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    PATH=/usr/local/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        xz-utils \
        python3 \
        python3-pip \
        python3-venv \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz" \
        -o /tmp/s6-noarch.tar.xz \
    && tar -C / -Jxpf /tmp/s6-noarch.tar.xz \
    && curl -fsSL "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz" \
        -o /tmp/s6-arch.tar.xz \
    && tar -C / -Jxpf /tmp/s6-arch.tar.xz \
    && rm /tmp/s6-noarch.tar.xz /tmp/s6-arch.tar.xz

RUN curl -fsSL -o /tmp/ngrok.tgz https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz \
    && tar -C /tmp -xzf /tmp/ngrok.tgz \
    && install -m 0755 /tmp/ngrok /usr/local/bin/ngrok-real \
    && rm /tmp/ngrok.tgz /tmp/ngrok

COPY artifacts/humanode-version.txt /build/humanode-version.txt
COPY build/fetch-upstream.sh /build/fetch-upstream.sh
RUN /build/fetch-upstream.sh \
    && rm -rf /build

RUN groupadd --system --gid 1100 hmnd \
    && useradd --system --uid 1100 --gid hmnd --home-dir /data --no-create-home --shell /usr/sbin/nologin hmnd \
    && groupadd --system --gid 1101 botuser \
    && useradd --system --uid 1101 --gid botuser --home-dir /var/empty --no-create-home --shell /usr/sbin/nologin botuser

COPY bot/ /opt/hmnd_bot/
RUN pip3 install --no-cache-dir --break-system-packages /opt/hmnd_bot

COPY rootfs/ /

RUN chmod 0755 /entrypoint.sh /usr/local/bin/insert-key.sh \
    && chmod 0755 /etc/s6-overlay/s6-rc.d/node/run /etc/s6-overlay/s6-rc.d/node/finish \
    && chmod 0755 /etc/s6-overlay/s6-rc.d/bot/run

VOLUME ["/data"]

EXPOSE 30333/tcp

ENTRYPOINT ["/entrypoint.sh"]
