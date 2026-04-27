#!/bin/sh
docker run -d \
  --name retirement-advisor \
  -p 8000:8000 \
  -v /mnt/user/appdata/retirement-mcp/data:/app/data \
  --env-file /mnt/user/appdata/retirement-mcp/.env \
  --restart unless-stopped \
  retirement-advisor
