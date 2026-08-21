#!/bin/bash
# 首次初始化脚本（docker-entrypoint-initdb.d，仅空数据卷执行）：
# 1) 扩展 + 中文全文配置（需 superuser）
# 2) 应用账号（非 superuser）——backend 默认 DSN 使用，最小权限原则
#    （密码请勿包含单引号；生产建议通过 APP_DB_PASSWORD 注入强口令）
set -e

# ── 扩展与中文全文配置（superuser 执行）─────────────────────────────
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'EOSQL'
-- pgvector 扩展（Week 4 RAG 使用，pgvector/pgvector:pg16 镜像自带）
CREATE EXTENSION IF NOT EXISTS vector;

-- zhparser 中文分词扩展（混合检索 Week 9）
CREATE EXTENSION IF NOT EXISTS zhparser;

-- 中文全文搜索配置（PG 不支持 CREATE TEXT SEARCH CONFIGURATION IF NOT EXISTS，用 DO 块代替）
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'chinese') THEN
        CREATE TEXT SEARCH CONFIGURATION chinese (PARSER = zhparser);
        ALTER TEXT SEARCH CONFIGURATION chinese
            ADD MAPPING FOR n,v,a,i,e,l,j WITH simple;
    END IF;
END
$$;
EOSQL

# ── 应用账号（非 superuser）─────────────────────────────────────────
APP_USER="${APP_DB_USER:-nexus_app}"
APP_PASSWORD="${APP_DB_PASSWORD:-nexus_app}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${APP_USER}') THEN
        CREATE ROLE ${APP_USER} LOGIN PASSWORD '${APP_PASSWORD}'
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
\$\$;
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${APP_USER};
-- CREATE：应用启动建表（create_all / alembic）需要；USAGE：访问 schema 内对象
-- （含使用 chinese 全文配置——text search 对象无独立 ACL，schema USAGE 即够）
GRANT USAGE, CREATE ON SCHEMA public TO ${APP_USER};
EOSQL
