FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONUTF8=1 \
    PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/

WORKDIR /app

# C 拆 apt 系统依赖层：显式安装 playwright chromium 运行所需的系统库
# 替代 `playwright install --with-deps` 的隐式 apt，后续 playwright install 只下二进制
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libxshmfence1 && \
    rm -rf /var/lib/apt/lists/*

# B Layer 顺序调整：playwright python 包版本固定，层稳定，几乎不重建
# A 镜像源加速：PLAYWRIGHT_DOWNLOAD_HOST 已在 ENV 中设置，chromium 175MB 走国内镜像
RUN pip install --no-cache-dir playwright~=1.60.0 \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn
RUN playwright install chromium

# 其他依赖：requirements.txt 频繁变化，独立一层
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
