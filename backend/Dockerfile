FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PYTHONUTF8=1 \
    PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/ \
    # 浏览器装到系统目录（非 /root/.cache）：非 root 运行用户也需要可读
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

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

# P1-10：非 root 运行（uid/gid 1000 与常见云主机用户对齐，便于 data 目录授权）
# outputs 一并预建：named volume 首挂时会继承镜像内属主；
# bind mount 场景镜像内容被遮蔽，需在宿主机预授权（见 doc/上线部署手册.md）
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/data/outputs \
    && chown -R appuser:appuser /app/data
USER appuser

EXPOSE 8000

# 生产启动：无 --reload（热重载不进生产；SSE/运行注册表为进程内存态，单 worker）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
