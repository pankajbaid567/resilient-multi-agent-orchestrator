# Backend on EC2 with an IAM role (no stored credentials)

- `t4g.small` (2 GB, Graviton, Amazon Linux 2023; resized from `t4g.micro` on 2026-11-19 to share the host with a second app) in `ap-south-1`, encrypted gp3 root volume, IMDSv2 required (hop limit 1).
- **Bedrock access** comes from the instance role:
  - The role allows `bedrock:InvokeModel*` on the three models only, plus `bedrock:CallWithBearerToken`.
  - The app turns the role's credentials into a short-term Bedrock key (`BEDROCK_ROLE_KEY_REGION`) and refreshes it every 45 minutes.
  - No API key is stored anywhere.
- **Network:** only ports 80 and 443 are open. Caddy terminates TLS with a Let's Encrypt certificate for `<elastic-ip-with-dashes>.sslip.io` and proxies to the app on `127.0.0.1:8000`. There is no SSH; management is through Systems Manager.
- **Public-demo limits:** 5 tasks per IP per hour, 50 tasks per day, and a 60k-token budget per task. Checkpoints are stored in SQLite on the instance disk.
- **Execution:** parallel steps on (`PARALLEL_MODE=true`), at most 4 per wave (`MAX_CONCURRENT_STEPS=4`) to stay within the instance's 1 GB of memory and the Bedrock request rate.
- **Changing settings** on the running instance: edit `/opt/backend.env` through Systems Manager, then recreate the `backend` container. `GET /config` shows what the server is using.
- **Cost:** about $8.65/month for the instance, the public IPv4 address and 10 GB of disk when it was a `t4g.micro` (ap-south-1 on-demand prices, November 2026); the `t4g.small` costs roughly twice the instance part (estimate; AWS Billing is the authority), plus Bedrock tokens (about $0.004 per full task). An AWS Budget alert fires at $5.

- **Shared host:** a second container (Codicil, port 8001) runs beside the backend, and Caddy routes `codicil.<ip-with-dashes>.sslip.io` to it. Container memory limits keep one app from starving the other.

`user-data.sh` is the bootstrap script; `__HOST__` and `__CORS__` are filled in at launch.

## Frontend

The UI is a static build on Cloudflare Pages (`reliable-agent.pages.dev`), built with
`VITE_API_URL=https://<host> npm run build` and deployed with
`wrangler pages deploy dist --project-name reliable-agent --branch main`.
`frontend/public/_headers` sets security headers and long caching for hashed assets.
The backend's `CORS_ORIGINS` must list the Pages origin. The WebSocket URL comes from the same
`VITE_API_URL`, with `https` becoming `wss`.
