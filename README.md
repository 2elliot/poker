# Poker Bots — Static Archive

This branch (`static-archive`) is the **archived, static** version of the Poker Bots site. It is what GitHub Pages serves at `poker.elliotd.net`.

The live, interactive Flask application lives on the `main` branch. This branch only contains the files needed for the static site.

## What's here

```
.
├── index.html              # Tournament viewer (replay of recorded matches)
├── leaderboard.html        # Leaderboard with placeholder data
├── CNAME                   # GitHub Pages custom domain (poker.elliotd.net)
├── static/
│   ├── css/styles.css
│   ├── js/
│   │   ├── scripts.js      # Tournament page — picks a transcript at random and replays it
│   │   ├── leaderboard.js  # Leaderboard page — reads /static/data/leaderboard.json
│   │   └── bot-profile.js  # Shared bot profile modal rendering
│   └── data/
│       ├── transcripts.json  # 8 pre-recorded tournament transcripts
│       ├── leaderboard.json  # Placeholder leaderboard rows
│       └── bots.json         # Placeholder per-bot stats (for the profile modal)
└── README.md
```

All match data and bot stats are **placeholder/template data**. The site shows a banner saying so on every page.

## Regenerating the data

The transcripts and placeholder stats are produced by `tools/generate_static_data.py` on the `main` branch (which still has the full Python engine). To regenerate:

```bash
git checkout main
python tools/generate_static_data.py
# Output lands in static_data/

git checkout static-archive
cp ../path/to/static_data/*.json static/data/
git add static/data/
git commit -m "Refresh static archive data"
```

## GitHub Pages setup

1. In repo Settings → Pages, set the source to **branch `static-archive`**, folder `/ (root)`.
2. `CNAME` in this branch tells GitHub Pages the custom domain is `poker.elliotd.net`.
3. At your DNS provider, add a `CNAME` record for `poker` → `<your-github-username>.github.io`.
4. Allow a few minutes for DNS + HTTPS provisioning.

## Local preview

```bash
python -m http.server 8000
# then open http://localhost:8000/
```
