# Deploy on Render (free demo)

The root [`render.yaml`](../render.yaml) defines two services: a free Django
Docker web service and a free Vite static site. It does **not** provision Redis
or Postgres. The app has no database; its optional cache is in-process here.
Render assigns both services HTTPS `onrender.com` URLs. The Blueprint passes the
API URL to the Vite build, and allows only the static site's origin through
Django CORS. Django reads the API's public hostname from Render's runtime
environment for `ALLOWED_HOSTS`. No API keys or secrets belong in Git; Render
generates the Django secret key.

## Set up

1. Push this deployment configuration to your GitHub repository. Sign in to
   [Render](https://dashboard.render.com/) and connect that repository.
2. Choose **New → Blueprint**, select the repository and its `main` branch, and
   review the two services from `render.yaml`. Confirm the API plan is **Free**
   before creating them. Do not add a paid datastore or disk.
3. Wait for the API health check and static build to finish. Check
   `https://<api-host>.onrender.com/api/v1/health/` and
   `https://<api-host>.onrender.com/api/v1/config/` in a browser. Then open the
   static site's URL and run a short trip such as Chicago to Detroit.
4. In browser devtools, verify the config and stream requests go to the API's
   HTTPS host and that the stream ends with `done`. If the site built before
   the API URL reference was populated, manually redeploy the **static site**;
   Vite embeds `VITE_API_BASE_URL` at build time.

## Configuration and checks

| Setting | Render value | Why |
|---|---|---|
| `VITE_API_BASE_URL` | API service's `RENDER_EXTERNAL_URL` | Browser sends config and NDJSON requests to the API, not to the static CDN. |
| `RENDER_EXTERNAL_HOSTNAME` | Set automatically by Render at runtime | Production settings add this exact hostname to Django's allowed hosts. Add custom API hostnames with `DJANGO_ALLOWED_HOSTS` if needed. Do not create a Blueprint self-reference: it can be empty on first provisioning. |
| `DJANGO_CORS_ALLOWED_ORIGINS` | Static service's `RENDER_EXTERNAL_URL` | Permits the frontend's cross-origin JSON POST and its preflight; do not use `*`. Add custom frontend origins explicitly. |
| `DJANGO_SECRET_KEY` | Render-generated | Required by production settings; do not commit it. |
| `WEATHER_PROVIDER` | `mock` | Fast and deterministic for a demo. Switch to `open_meteo` for live forecasts after checking service latency and provider usage. |
| `CACHE_BACKEND` | `locmem` | No paid Redis service; results are lost on restart, and caches are per worker. |

Render terminates HTTPS before the container. The API listens on `0.0.0.0:8000`
with `PORT=8000`; Django's SSL redirect remains off because Render handles TLS.
The backend Docker image is unchanged for local Compose, while Render overrides
its default two-worker command with **one worker and two threads** to fit the
512 MB free instance. The frontend's same-origin `/api` behavior is preserved
when `VITE_API_BASE_URL` is unset (local Compose and Vite proxy).

## Free-tier caveats

- The API sleeps after about 15 minutes without traffic. Its first request can
  take roughly a minute to wake, *before* the analysis begins; load the site
  and warm the API health endpoint before recording a demo.
- A free API has limited CPU, memory, outbound traffic, and concurrency. The
  stream holds one of its two threads while it runs. This is a portfolio demo,
  not a reliable production service. Render may suspend unusually high outbound
  provider traffic; review its [free-tier limits](https://render.com/docs/free).
- No persistent data is required, but the in-memory cache disappears on sleep,
  restart, or redeploy. A static site alone cannot run the Django API.
- With `WEATHER_PROVIDER=open_meteo`, forecasts may be slow or unavailable and
  external provider limits apply. A weather fetch failure is displayed as an
  **unavailable** warning, not an assurance that conditions are safe.

If the browser reports a CORS error, compare its `Origin` header against the
API service's `DJANGO_CORS_ALLOWED_ORIGINS`; both must be exact HTTPS origins
without trailing paths or slashes. If a request hits the static site instead of
the API, check the static service's build-time `VITE_API_BASE_URL` and redeploy
it. If the API returns `DisallowedHost`, check its runtime
`RENDER_EXTERNAL_HOSTNAME` against the hostname in the request URL; as an
immediate dashboard workaround, set `DJANGO_ALLOWED_HOSTS` to the exact API
hostname (no `https://` or trailing slash) and redeploy.
