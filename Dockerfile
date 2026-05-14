FROM python:3.9-slim

WORKDIR /app

# 安装必要的依赖库
RUN pip install --no-cache-dir flask requests

COPY app.py .

# 暴露给宿主机的端口
EXPOSE 18089

CMD ["python", "app.py"]
