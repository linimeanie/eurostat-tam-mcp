# Deploy the Eurostat connector for the web (claude.ai)

The web app can only talk to a **public HTTPS URL**, so the server has to live
on a host. This guide uses **Render** (free tier). End result: a URL like
`https://eurostat-tam-mcp.onrender.com/mcp` that you paste into claude.ai.

> On the web connector you get the two **data** tools
> (`get_enterprise_counts`, `get_segment_counts`). The `fill_tam_sheet` tool is
> desktop-only — a cloud server can't write to your Mac's files.

---

## Step 1 — Put the code on GitHub

The local git repo is already created and committed for you. You just need to
create an empty GitHub repo and push.

1. Go to https://github.com/new → name it `eurostat-tam-mcp` → **Create**
   (leave it empty: no README/license).
2. Back in Terminal, run the two lines GitHub shows under *"…or push an existing
   repository"* — they look like:

   ```bash
   cd /Users/lini/Documents/Claude/eurostat-mcp
   git remote add origin https://github.com/<YOUR-USERNAME>/eurostat-tam-mcp.git
   git branch -M main
   git push -u origin main
   ```

## Step 2 — Deploy on Render

1. Sign up / log in at https://render.com (free; you can use "Sign in with GitHub").
2. Click **New +** → **Blueprint**.
3. Connect your GitHub and pick the `eurostat-tam-mcp` repo. Render reads
   `render.yaml` and fills everything in automatically.
4. Click **Apply** / **Create**. Wait ~2–4 min for the first build.
5. When it's live, copy the service URL at the top, e.g.
   `https://eurostat-tam-mcp.onrender.com`.

   **Your connector URL is that + `/mcp`:**
   `https://eurostat-tam-mcp.onrender.com/mcp`

   Quick check — open in a browser; you should see a small JSON error like
   *"Missing session ID"* (that means it's up; a blank/refused page means it isn't).

## Step 3 — Add it to claude.ai

1. In the web app: **Settings → Connectors → Add custom connector**.
2. Name: `Eurostat TAM`. URL: paste the `…/mcp` URL from Step 2.
3. Save. Open a new chat and try:

   > "Use Eurostat TAM to pull C27 for EU27+NO+CH."

   You should get back `sme_count` / `corp_250plus_count`.

---

## Things to know

- **Cold start:** Render's free tier sleeps after ~15 min idle, so the *first*
  call after a quiet spell can take ~50s to wake. Subsequent calls are fast.
  Upgrade the Render plan (or ping the URL on a schedule) if that's annoying.
- **It's public & unauthenticated:** anyone with the URL can call it. That's
  fine here — it only returns public Eurostat figures — but don't add private
  data tools to this server without putting auth in front.
- **Sharing with your CMO:** once it's added as a connector, sharing depends on
  your Claude plan's connector-sharing (Team/Enterprise can share org
  connectors; otherwise each person adds the same URL themselves).
- **Updating it later:** push to GitHub and Render redeploys automatically.
