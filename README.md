# Netflix-style Movie/Natok/Series Telegram Bot

Ad-unlock system: video dekhte hole user ke 2ta ad task complete korte hoy
(protita task e link real-e open + kompokkhe 15 sec dure thakte hoy —
Page Visibility API diye check hoy, shudhu wait kore bypass kora jay na),
tarpor video 10 minute er jonno unlock hoy. Video file gulo private
storage group/channel e thake, bot shudhu `copy_message` diye ek jon
user ke pathay — kokhono public link/forward hoy na. Shob metadata
(title, category, description, thumbnail, ad links) **MongoDB**-e store
hoy, tai bot restart/redeploy korle data hariye jay na.

## Setup

1. Python 3.10+ install thakte hobe.
2. Dependencies install korun:
   ```
   pip install -r requirements.txt
   ```
3. [@BotFather](https://t.me/BotFather) diye ekta bot toiri kore token nin.
4. **MongoDB Atlas**-e (free tier) ekta cluster banan, connection string
   (`mongodb+srv://...`) copy korun. Eta ekta secret — kokhono GitHub-e
   commit korben na, shudhu environment variable hisebe rakhben.
5. Ekta **private Telegram group/channel** toiri korun (media storage
   er jonno), bot ke oi group-e **member/admin** banan.
   - Chat-er numeric ID ber korte: bot deploy kore admin hisebe `/chatid`
     command diye group-e type korun, bot reply-te ID dibe.
6. Environment variables set korun:
   ```
   export BOT_TOKEN="123456:ABC-your-bot-token"
   export MONGODB_URI="mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/dbname"
   export ADMIN_IDS="111111111,222222222"   # apnar/admin der Telegram numeric user id
   export STORAGE_CHAT_ID="-1001234567890"  # private group/channel id
   export AD_WAIT_SECONDS="15"              # optional, default 15
   export UNLOCK_TTL_SECONDS="600"          # optional, default 600 (10 min)
   ```
7. Run:
   ```
   python bot.py
   ```

## Notun bot-e switch korle ki hobe

Video/title/category/description/ad-link shob **MongoDB**-e thake, ar
video file-gulo **storage group**-e thake — duitai bot token-er upor
depend kore na. Tai notun bot banale:
1. Notun bot-ke shei ekই storage group-e add korun (member hisebe)
2. Shei ekই `MONGODB_URI` notun bot-er environment-e set korun
3. `ADMIN_IDS`, `STORAGE_CHAT_ID` shei same rakhun

— shob purono video/metadata automatically notun bot-e dekha jabe,
kono migration script lagbe na.

## User flow

- `/start` → category buttons (Movie / Natok / Series)
- Category e click korle list dekhabe → video select korle thumbnail + info
- **Unlock & Watch** e click:
  - Task 1: Ad link 1 open + 15 sec wait + "✅ Dekhechi" click
  - Task 2: Ad link 2 open + 15 sec wait + "✅ Dekhechi" click
  - Dutoi complete hole video automatically pathiye deya hoy, ebong
    user er "My History" chart e add hoye jay.
  - 15 second timer **server-side** (database timestamp diye) check kora hoy,
    tai button just click korei bypass kora jay na.
  - Ekbar unlock hoye gele, oi user abar oi video free te dekhte parbe.

## Admin panel

Admin (ADMIN_IDS list e thaka user) der jonno:

- `/addvideo` — conversation shuru hobe:
  1. Title
  2. Category (button diye)
  3. Thumbnail image
  4. Ad Link 1
  5. Ad Link 2
  6. Video file (upload/forward) — automatically private storage channel e
     copy hoye jabe
- `/listvideos` — shob video-r ID + title dekhabe
- `/delvideo <id>` — video delete
- `/cancel` — chalu add-video process বাতিল

## Render e Free Deploy (24/7 alive)

⚠️ **Bot token GitHub e kokhono commit korben na.** `.env` file ta
`.gitignore` e already ache — GitHub e শুধু `.env.example` (khali
placeholder) jabe, আসল token `Render Dashboard → Environment` e secret
hisebe বসবেন.

1. Ei folder ta GitHub repo hisebe push korun (`.env` chara).
2. [render.com](https://render.com) e giye **New → Web Service** → GitHub
   repo select করুন. `render.yaml` thakle Render nijei settings detect
   kore nebe.
3. Environment variables tab e boson:
   - `BOT_TOKEN`
   - `MONGODB_URI`
   - `ADMIN_IDS`
   - `STORAGE_CHAT_ID`
4. Deploy hoye gele Render ekta public URL dibe
   (`https://your-service.onrender.com`) — eta automatically
   `RENDER_EXTERNAL_URL` hisebe bot er kache pouche jay.
5. Bot nijer `/health` endpoint ke **prottek 2 minute** por por self-ping
   kore (`keepalive.py`), tai free plan er 15-min idle-spin-down er
   somossa hoy na, bot 24/7 alive thake.
   - Extra safety hisebe [cron-job.org](https://cron-job.org) ba
     [UptimeRobot](https://uptimerobot.com)-e free account kore
     `https://your-service.onrender.com/health` ke every 5 min ping
     korte parben (optional, double-layer protection).

## Ad tracking

`/adstats` (admin only) — protita video-r protita ad slot (Ad1/Ad2) er
"shown" (impression) ebong "clicked" count dekhabe, DB theke.
Eta ekta **in-house/first-party tracker** — jodi apni kono specific
3rd-party ad-network (jemon Adsterra, Monetag, Adsterra Direct Link, etc.)
use korchen ar oder postback/API diye real-click-verify korte chan, seta
network-specific integration — kon network use korchen janale seta
add kore dite pari.

## Notes / limitations

- Telegram bot API die "user ta real e ad dekheche kina" 100% guarantee kora
  jay na — eta shob ad-unlock bot er common limitation. Ei bot 15-second
  server-side wait enforce kore, jeta basic bypass (fast double-click) thamay.
  Aro strong verification lagle (jemon: 3rd-party ad-network SDK/postback),
  seta alada integration hisebe add korte hobe.
- Video file size Telegram bot API te ~50MB (local bot API server use korle
  beshi) porjonto support kore.
- Production e deploy korte VPS/server e `python bot.py` ke `systemd`,
  `pm2`, ba `supervisor` diye run rakhte paren, jate bot 24/7 online thake.
