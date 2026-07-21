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
