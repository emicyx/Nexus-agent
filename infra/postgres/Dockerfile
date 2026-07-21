FROM pgvector/pgvector:pg16

# 编译依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential wget bzip2 ca-certificates git \
        postgresql-server-dev-16 \
    && rm -rf /var/lib/apt/lists/*

# SCWS（zhparser 依赖）
RUN cd /tmp \
    && wget -q --tries=3 --retry-connrefused \
        http://www.xunsearch.com/scws/down/scws-1.2.3.tar.bz2 \
    && tar -xjf scws-1.2.3.tar.bz2 \
    && cd scws-1.2.3 \
    && ./configure && make -j"$(nproc)" && make install \
    && ldconfig \
    && rm -rf /tmp/scws-1.2.3*

# zhparser
RUN cd /tmp \
    && git clone --depth 1 https://github.com/amutu/zhparser.git \
    && cd zhparser \
    && make PG_CONFIG=/usr/lib/postgresql/16/bin/pg_config \
    && make PG_CONFIG=/usr/lib/postgresql/16/bin/pg_config install \
    && rm -rf /tmp/zhparser
