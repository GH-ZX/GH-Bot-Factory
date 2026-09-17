# Local Domain Reverse Proxy

This temporary deployment keeps GH-Bot-Factory reachable only on the local network while the public tunnel is unavailable. It uses a dedicated subdomain so the existing React application at `gh-store.me` remains unchanged.

## Target topology

```text
LAN phone/browser
  -> UDM local DNS: bot.gh-store.me = 10.70.5.5
  -> Nginx Proxy Manager on 10.70.5.5:443
  -> http://10.70.5.5:8010
  -> GH-Bot-Factory API/Admin/Mini App
```

Only clients using the UDM as their DNS resolver and able to reach the LAN can use this temporary endpoint. A phone on mobile data or an external network needs VPN access or the restored public tunnel.

## 1. TLS certificate

Telegram Web Apps require HTTPS. A local DNS record does not issue a trusted certificate by itself.

In Nginx Proxy Manager, obtain or select a certificate for `bot.gh-store.me`. When public HTTP validation is unavailable, use a DNS-01 challenge with a narrowly scoped DNS-provider token. Keep that token in Nginx Proxy Manager and never place it in this repository or `.env`.

## 2. Nginx Proxy Manager host

Create one Proxy Host with:

- Domain: `bot.gh-store.me`
- Scheme: `http`
- Forward hostname/IP: `10.70.5.5`
- Forward port: `8010`
- WebSocket support: enabled
- SSL certificate: the trusted `bot.gh-store.me` certificate
- Force SSL: enabled
- HTTP/2: enabled when available

Do not change or replace the existing Nginx Proxy Manager container. It already owns host ports 80, 81, and 443.

## 3. UDM Pro Max local DNS

Create a Host (A) record:

- Domain name: `bot.gh-store.me`
- IPv4 address: `10.70.5.5`

On UniFi Network 9.4 this is under **Settings -> Policy Table -> Create New Policy -> DNS**. On 9.3 it is under **Settings -> Policy Engine -> DNS -> Create DNS Record**.

Clients must use the UDM as their DNS resolver. Reconnect the phone to Wi-Fi or renew its DHCP lease after adding the record. Private/secure DNS on the phone may bypass the UDM and must not override the local answer during testing.

## 4. Application cutover

After DNS, TLS, and proxy checks succeed, set these destination-owned `.env` values:

```text
APP_ENV=staging
MINIAPP_PUBLIC_URL=https://bot.gh-store.me/miniapp/
ADMIN_PUBLIC_URL=https://bot.gh-store.me/admin/
RATE_LIMIT_ENABLED=true
RATE_LIMIT_BACKEND=redis
API_BIND_ADDRESS=10.70.5.5
FORWARDED_ALLOW_IPS=127.0.0.1,172.22.0.0/16
```

The proxy network is currently `172.22.0.0/16`. Re-check it if Nginx Proxy Manager is reinstalled or its Compose network changes.

Recreate only this project's services:

```bash
docker compose up -d --force-recreate api worker bot-runtime
```

## 5. Verification

From a LAN client using UDM DNS, verify:

```text
https://bot.gh-store.me/health/ready
https://bot.gh-store.me/admin/
https://bot.gh-store.me/miniapp/
```

Then request a fresh Telegram `/admin` link and confirm direct sign-in, refresh persistence, and Exit. Run `make doctor-live` on the host and inspect `docker compose ps` before treating the local endpoint as ready.

## Rollback

If local HTTPS fails, leave `APP_ENV=development`, keep the existing LAN Admin URL, and remove or disable only the new `bot.gh-store.me` proxy/DNS entries. Do not change the React application's apex-domain records or the existing Cloudflare tunnel while using this temporary path.
