# Backend on EC2 with an IAM role (no stored credentials)

- `t4g.micro` (Graviton, Amazon Linux 2023) in `ap-south-1`, encrypted gp3 root volume, IMDSv2 required (hop limit 1).
- **Bedrock access** comes from the instance role:
  - The role allows `bedrock:InvokeModel*` on the three models only, plus `bedrock:CallWithBearerToken`.
  - The app turns the role's credentials into a short-term Bedrock key (`BEDROCK_ROLE_KEY_REGION`) and refreshes it every 45 minutes.
  - No API key is stored anywhere.
- **Network:** only ports 80 and 443 are open. Caddy terminates TLS with a Let's Encrypt certificate for `<elastic-ip-with-dashes>.sslip.io` and proxies to the app on `127.0.0.1:8000`. There is no SSH; management is through Systems Manager.
- **Public-demo limits:** 5 tasks per IP per hour, 50 tasks per day, and a 60k-token budget per task. Checkpoints are stored in SQLite on the instance disk.
- **Cost:** about $8.65/month for the instance, the public IPv4 address and 10 GB of disk (ap-south-1 on-demand prices, November 2026), plus Bedrock tokens (about $0.004 per full task). An AWS Budget alert fires at $5.

`user-data.sh` is the bootstrap script; `__HOST__` and `__CORS__` are filled in at launch.
