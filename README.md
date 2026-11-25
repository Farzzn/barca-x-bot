# Barca X Auto-Poster + Vercel Admin Dashboard (`barca-x-bot`)

Full-stack automated FC Barcelona Football Twitter engine:
- **Heroku Python Worker** (separate Heroku app, `0` always-on dynos, free **Heroku Scheduler** every 10 minutes — zero interruption to your existing Telegram bot).
- **Next.js Admin Interface (`admin/`)** hosted on **Vercel** — control posting behavior live, monitor **Gemini Primary vs Fallback API key activity**, test 1-click previews, and upload player/rival images straight to **GitHub**.
- **Shared Free PostgreSQL (Neon / Supabase)** — syncs live settings, Half-Time/Full-Time event locks, and post/API-key telemetry between Heroku and Vercel for **\$0/month**.

---

## Key Features

1. **Instant Half-Time (HT) & Full-Time (FT) Triggers**
   - Heroku Scheduler runs `python run_post.py` **every 10 minutes** (~3.75 Eco dyno hrs/month out of 1,000 shared hours = **\$0 extra**).
   - Because half-time lasts 15 minutes, a 10-minute check guarantees catching **every Barça Half-Time** and **every Full-Time within 0–10 minutes of the final whistle**, automatically pulling breaking post-match Barça headlines (ESPN + RSS + Google News) to fire an instant viral shitpost!
   - Exact-once DB locks (`match_events_sent`) ensure HT and FT each fire once per match.
2. **Dual Gemini API Keys with Automatic Failover + Activity Log**
   - Uses `BARCA_GEMINI_API_KEY` (Key #1 Primary) and automatically fails over to `BARCA_GEMINI_API_KEY_2` (Key #2 Fallback) on quota/auth/API errors.
   - Automatically falls back from `gemini-4-hydrogen` to `gemini-3.5-flash-lite` during high-demand `503` spikes.
   - Every post logs `gemini_key_used` (`primary` / `fallback`), `gemini_fallback_reason`, `gemini_model`, and `trigger_type` — visible with color-coded badges in the Vercel Admin UI.
3. **Vercel Next.js Admin Dashboard (`admin/`)**
   - Password-protected (`ADMIN_PASSWORD` + signed `httpOnly` cookie).
   - **Activity & API Keys Tab:** KPI cards (Posts Today, Key #1 vs Key #2 usage %, HT/FT count, est. X API spend), live Barça & Rival match radar, filterable activity feed, and 1-click Dry-Run Preview generator.
   - **Posting Behavior Tab:** Pause/Resume kill switch, Dry-Run mode, HT/FT/Pre-Match/Rival toggles, daily cap (1–15), cooldowns, clickable 24-hour UTC slot grid, chaos level (`balanced` / `spicy` / `unhinged`), image ratio slider, archetype checkboxes, Gemini model selector, and custom weekly banter directive.
   - **GitHub Images Tab:** Upload `.jpg/.png/.webp` photos with player/club/mood tags directly to GitHub (`assets/images/<category>/`) + `assets/catalog.json`. Heroku fetches new GitHub images automatically at runtime — **no Heroku redeploy needed** when you upload photos from your phone!

---

## Step 1: Create Your Free Shared Database (Neon Postgres — 2 min)

Both Heroku and Vercel need the **same** PostgreSQL `DATABASE_URL` (so you don't spend \$5/mo of your Heroku credits on a second Heroku Postgres add-on):

1. Sign up free at **[neon.tech](https://neon.tech/)** (no credit card required).
2. Create a project (e.g. `barca-x-bot`) and copy the **Connection String** (`postgresql://...?sslmode=require`).
3. Both the Python bot and the Next.js Admin app auto-create the tables (`bot_settings`, `x_post_history`, `match_events_sent`) on first run.

---

## Step 2: Push This Repo to GitHub & Create a GitHub Token (for Image Uploads)

1. Push `barca-x-bot/` (including `admin/`) to a GitHub repo (public or private — both work!):
   ```bash
   cd C:\Users\Tutorialspoint\Desktop\Projects\barca-x-bot
   git init
   git add .
   git commit -m "Barca X auto-poster + Vercel admin dashboard"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/barca-x-bot.git
   git push -u origin main
   ```
   *(Make sure `.env` is NOT committed — it is already listed in `.gitignore`!)*
2. Create a Fine-Grained Personal Access Token at **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**:
   - **Repository access:** Only select repositories → `barca-x-bot`
   - **Repository permissions → Contents:** **Read and write**
   - Copy the generated token (`github_pat_...`) as `GITHUB_TOKEN`.

---

## Step 3: Deploy the Bot to a Separate Heroku App (Zero Impact on `diana-backend`)

```bash
cd C:\Users\Tutorialspoint\Desktop\Projects\barca-x-bot

# Create a brand-new Heroku app isolated from your Telegram bot
heroku create your-barca-x-bot

# Set Config Vars (use your BarcaBanterBot Pay Per Use X keys + both Gemini keys + Neon DB + GitHub)
heroku config:set \
  BARCA_GEMINI_API_KEY="YOUR_KEY_1" \
  BARCA_GEMINI_API_KEY_2="YOUR_KEY_2" \
  GEMINI_MODEL="gemini-3.5-flash-lite" \
  ENABLE_GOOGLE_SEARCH="false" \
  X_API_KEY="YOUR_X_API_KEY" \
  X_API_SECRET="YOUR_X_API_SECRET" \
  X_ACCESS_TOKEN="YOUR_X_ACCESS_TOKEN" \
  X_ACCESS_TOKEN_SECRET="YOUR_X_ACCESS_TOKEN_SECRET" \
  DATABASE_URL="YOUR_NEON_POSTGRES_URL" \
  GITHUB_REPO="YOUR_USERNAME/barca-x-bot" \
  GITHUB_BRANCH="main" \
  GITHUB_TOKEN="YOUR_GITHUB_PAT"

# Deploy & keep 0 always-on dynos running
git push heroku main
heroku ps:scale web=0 worker=0

# Smoke test safely in dry-run mode
heroku run "python run_post.py --check"
heroku run "python run_post.py --dry-run --force-now"

# Add free Heroku Scheduler add-on
heroku addons:create scheduler:standard
heroku addons:open scheduler
```

In the **Heroku Scheduler** UI:
- Click **Create job**
- **Schedule:** **Every 10 minutes** *(guarantees catching every 15-min Half-Time & immediate Full-Time whistle!)*
- **Run Command:** `python run_post.py`

---

## Step 4: Deploy the Next.js Admin Interface to Vercel (2 min)

1. Go to **[vercel.com/new](https://vercel.com/new)** and import your `barca-x-bot` GitHub repository.
2. **CRITICAL:** Set **Root Directory** to **`admin`**!
3. Add these **Environment Variables** in Vercel:
   - `ADMIN_PASSWORD` = *(your strong dashboard login password)*
   - `SESSION_SECRET` = *(any random 32+ char string)*
   - `DATABASE_URL` = *(the EXACT same Neon Postgres URL used on Heroku)*
   - `GITHUB_REPO` = `YOUR_USERNAME/barca-x-bot`
   - `GITHUB_BRANCH` = `main`
   - `GITHUB_TOKEN` = *(your `github_pat_...` with Contents Read/Write)*
   - `BARCA_GEMINI_API_KEY` = *(optional — enables 1-click Preview inside the dashboard)*
   - `BARCA_GEMINI_API_KEY_2` = *(optional fallback key for dashboard previews)*
4. Click **Deploy**! Open your Vercel URL, log in, upload your Barça & rival photos under **GitHub Images**, and tune your schedule & chaos level live!

---

## Local Development Commands

```powershell
# Python bot
cd C:\Users\Tutorialspoint\Desktop\Projects\barca-x-bot
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python run_post.py --check
.\.venv\Scripts\python run_post.py --dry-run --force-now --trigger fulltime

# Next.js Admin Dashboard locally (http://localhost:3000)
cd C:\Users\Tutorialspoint\Desktop\Projects\barca-x-bot\admin
Copy-Item .env.example .env.local
npm run dev
```
