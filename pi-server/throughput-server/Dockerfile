FROM nginx:alpine

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Generate a 50 KB random binary payload for ESP32 throughput tests.
# 50 * 1024 = 51200 bytes.  /dev/urandom is always available in Linux builds.
RUN dd if=/dev/urandom of=/usr/share/nginx/html/test_payload.bin bs=1024 count=50 \
    && echo "OK" > /usr/share/nginx/html/health

EXPOSE 80
