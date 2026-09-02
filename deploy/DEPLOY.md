# Deploying to a 1GB DigitalOcean droplet

Ubuntu 24.04 droplet, 1GB RAM / 1 vCPU assumed throughout. That RAM budget is
the thing every choice below is made around — Postgres + 2 gunicorn workers +
nginx all resident at once leaves very little slack, so the swap file in
step 1 isn't optional.

## 1. Base server setup

```bash
adduser ternah                      # deploy user, not root
usermod -aG sudo ternah
su - ternah

# Swap file — without this, a bad moment (e.g. running collectstatic and a
# request spike at the same time) can OOM-kill postgres or gunicorn outright.
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

sudo apt update && sudo apt install -y python3-venv python3-dev libpq-dev \
    postgresql postgresql-contrib nginx git ufw

sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

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
specifically (`sudo systemctl status postgresql` / check `free -h`).

## 3. App code

```bash
git clone <your-repo-url> ~/ternah_factories
cd ~/ternah_factories
python3 -m venv venv
venv/bin/pip install -r requirements.txt

cp .env.example .env
nano .env   # fill in DJANGO_SECRET_KEY, DB_PASSWORD, ALLOWED_HOSTS, etc.
```

Generate a real secret key rather than hand-typing one:

```bash
venv/bin/python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

```bash
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser
```

## 4. gunicorn as a systemd service

```bash
sudo cp deploy/gunicorn.service /etc/systemd/system/ternah-gunicorn.service
sudo systemctl daemon-reload
sudo systemctl enable --now ternah-gunicorn
sudo systemctl status ternah-gunicorn   # confirm it's running before moving on
```

## 5. nginx + HTTPS

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/ternah
sudo nano /etc/nginx/sites-available/ternah   # replace your-domain.com
sudo ln -s /etc/nginx/sites-available/ternah /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

Certbot rewrites the nginx config to add the HTTPS server block and redirect
automatically — that's expected, don't restore your original file afterward.

Once you've confirmed `https://your-domain.com` actually works end to end,
go back to `.env` and raise `DJANGO_HSTS_SECONDS` (e.g. to `31536000`, one
year). Leave it at `0` until then — HSTS is a one-way door per browser that's
painful to undo if HTTPS turns out to be broken.

## 6. Ongoing deploys

```bash
cd ~/ternah_factories
git pull
venv/bin/pip install -r requirements.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart ternah-gunicorn
```

## 7. Sanity checks after first deploy

- `curl -I https://your-domain.com/accounts/login/` → `200`
- `sudo journalctl -u ternah-gunicorn -n 50` — no import errors on boot
- `free -h` — confirm swap isn't being hammered continuously (occasional use
  under load is fine; constant high swap use means the droplet has genuinely
  outgrown 1GB and it's time to resize, not tune further)
- Log in and click through POS, a Production batch, and a Manager report —
  the things most likely to surface a settings mistake (DB write, static
  asset, session cookie) fastest.
