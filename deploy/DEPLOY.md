# Deploying to a 1GB DigitalOcean droplet

Ubuntu 24.04 droplet, 1GB RAM / 1 vCPU assumed throughout. That RAM budget is
the thing every choice below is made around — Postgres + 2 gunicorn workers +
nginx all resident at once leaves very little slack, so the swap file in
step 1 isn't optional.

Paths below assume you're deployed as **root**, repo cloned to
`/root/ternah-for-factories` — matching an actual droplet already set up this
way. Running the app as root works and is a common shortcut on a single-
operator droplet, but it does mean gunicorn (and anything it calls) runs with
full system privileges — if that ever matters to you, the harder-but-safer
version creates a dedicated `ternah` user and runs everything under
`/home/ternah/...` instead. Not required to get running today.

## 1. Base server setup

```bash
# Swap file — without this, a bad moment (e.g. running collectstatic and a
# request spike at the same time) can OOM-kill postgres or gunicorn outright.
fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

apt update && apt install -y python3-venv python3-dev libpq-dev \
    postgresql postgresql-contrib postgresql-client nginx git ufw

ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw enable
```

This is the step you haven't run yet — that's why `psql --version` came back
"not found": the `postgresql` package (server + client) was never installed,
only assumed. Run the block above now; `psql --version` will resolve
afterward.

## 2. PostgreSQL

```bash
sudo -u postgres psql -c "CREATE DATABASE ternah;"
sudo -u postgres psql -c "CREATE USER ternah WITH PASSWORD 'pick-a-real-password';"
sudo -u postgres psql -c "ALTER DATABASE ternah OWNER TO ternah;"
```

Defaults from the apt package (`shared_buffers=128MB`, `max_connections=100`)
are already conservative enough for this box — with `CONN_MAX_AGE=60` and 2
gunicorn workers × 2 threads, you'll only ever have a handful of connections
open. No tuning needed unless you later see memory pressure from Postgres
specifically (`systemctl status postgresql` / check `free -h`).

## 3. App code

You've already got this cloned to `~/ternah-for-factories` (i.e.
`/root/ternah-for-factories`) — the `deploy/gunicorn.service` and
`deploy/nginx.conf` files in this repo are written to match that exact path.
If you ever move the checkout, update those two files to match — they don't
derive the path automatically.

```bash
cd ~/ternah-for-factories
python3 -m venv venv
venv/bin/pip install -r requirements.txt

cp .env.example .env
nano .env   # fill in DJANGO_SECRET_KEY, DB_PASSWORD, ALLOWED_HOSTS, etc.
```

Generate a real secret key rather than hand-typing one:

```bash
venv/bin/python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Set `DB_ENGINE=postgres`, `DB_NAME=ternah`, `DB_USER=ternah`, `DB_PASSWORD=`
(what you picked in step 2) in `.env`, then:

```bash
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser
```

## 4. gunicorn as a systemd service

```bash
cp deploy/gunicorn.service /etc/systemd/system/ternah-gunicorn.service
systemctl daemon-reload
systemctl enable --now ternah-gunicorn
systemctl status ternah-gunicorn   # confirm it's running before moving on
```

If it fails to start, `journalctl -u ternah-gunicorn -n 50` almost always
shows why — most commonly a missing `.env` value or the venv not existing yet
at the path the service file expects.

## 5. nginx + HTTPS

```bash
cp deploy/nginx.conf /etc/nginx/sites-available/ternah
nano /etc/nginx/sites-available/ternah   # replace your-domain.com
ln -s /etc/nginx/sites-available/ternah /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

apt install -y certbot python3-certbot-nginx
certbot --nginx -d your-domain.com -d www.your-domain.com
```

Certbot rewrites the nginx config to add the HTTPS server block and redirect
automatically — that's expected, don't restore your original file afterward.

Once you've confirmed `https://your-domain.com` actually works end to end,
go back to `.env` and raise `DJANGO_HSTS_SECONDS` (e.g. to `31536000`, one
year). Leave it at `0` until then — HSTS is a one-way door per browser that's
painful to undo if HTTPS turns out to be broken.

## 6. Ongoing deploys

```bash
cd ~/ternah-for-factories
git pull
venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
systemctl restart ternah-gunicorn
```

## 7. Sanity checks after first deploy

- `curl -I https://your-domain.com/accounts/login/` → `200`
- `journalctl -u ternah-gunicorn -n 50` — no import errors on boot
- `free -h` — confirm swap isn't being hammered continuously (occasional use
  under load is fine; constant high swap use means the droplet has genuinely
  outgrown 1GB and it's time to resize, not tune further)
- Log in and click through POS, a Production batch, and a Manager report —
  the things most likely to surface a settings mistake (DB write, static
  asset, session cookie) fastest.
