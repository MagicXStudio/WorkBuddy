FROM locustio/locust:2.29.1

# 复用官方 locust 镜像，仅替换压测脚本
WORKDIR /mnt/locust
COPY locustfile.py /mnt/locust/locustfile.py

# 默认无界面直跑；参数可用环境变量覆盖
ENV LOCUST_HOST=http://127.0.0.1:7009 \
    LOCUST_USERS=100 \
    LOCUST_SPAWN_RATE=10 \
    LOCUST_RUN_TIME=5m

EXPOSE 8089
