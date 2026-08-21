FROM pgvector/pgvector:pg16

# 编译依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential wget bzip2 ca-certificates git \
        postgresql-server-dev-16 \
    && rm -rf /var/lib/apt/lists/*

# SCWS（zhparser 依赖）
# 供应链加固：HTTPS 下载 + SHA256 校验（防传输篡改/投毒）
ARG SCWS_VERSION=1.2.3
ARG SCWS_SHA256=60d50ac3dc42cff3c0b16cb1cfee47d8cb8c8baa142a58bc62854477b81f1af5
RUN cd /tmp \
    && wget -q --tries=3 --retry-connrefused \
        "https://www.xunsearch.com/scws/down/scws-${SCWS_VERSION}.tar.bz2" \
    && echo "${SCWS_SHA256}  scws-${SCWS_VERSION}.tar.bz2" | sha256sum -c - \
    && tar -xjf "scws-${SCWS_VERSION}.tar.bz2" \
    && cd "scws-${SCWS_VERSION}" \
    && ./configure && make -j"$(nproc)" && make install \
    && ldconfig \
    && rm -rf /tmp/scws-${SCWS_VERSION}*

# zhparser
# 供应链加固：固定 commit（master 浮动可能引入未经评估的变更/破坏）
ARG ZHPARSER_COMMIT=2e995c4df672563992b4d7a147b8fa2d0d4cda6c
RUN cd /tmp \
    && git clone https://github.com/amutu/zhparser.git \
    && cd zhparser \
    && git checkout "${ZHPARSER_COMMIT}" \
    && make PG_CONFIG=/usr/lib/postgresql/16/bin/pg_config \
    && make PG_CONFIG=/usr/lib/postgresql/16/bin/pg_config install \
    && rm -rf /tmp/zhparser
