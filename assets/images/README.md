# Player & Meme Image Library (`assets/images/`)

Drop JPG, PNG, or WebP photos into these 4 subfolders — the bot **automatically discovers** every image and lets Gemini pick the exact 1 or 2 images that match each post (e.g., 1 frustrated player photo during a post-match meltdown, or 2 side-by-side photos for a Barca vs Real Madrid comparison bait post!).

## Naming Convention (Auto-Tagging Magic)

Name files as `<player_or_topic>_<mood>.jpg` and the bot auto-detects the player, club, and emotional tone:

### `barca/` (FC Barcelona players)
- `lamine_yamal_celebrating.jpg`
- `lamine_yamal_cold.jpg`
- `pedri_serious.jpg`
- `pedri_smiling.jpg`
- `raphinha_celebrating.jpg`
- `raphinha_frustrated.jpg`
- `lewandowski_frustrated.jpg`
- `lewandowski_celebrating.jpg`
- `gavi_angry.jpg`
- `frenkie_de_jong_serious.jpg`
- `cubarsi_focused.jpg`
- `fermin_lopez_celebrating.jpg`
- `dani_olmo_celebrating.jpg`

### `rivals/` (Real Madrid, Atleti & UCL rivals)
- `vinicius_complaining.jpg`
- `vinicius_crying.jpg`
- `mbappe_shocked.jpg`
- `mbappe_frustrated.jpg`
- `bellingham_frustrated.jpg`
- `bellingham_arms_up.jpg`
- `courtois_angry.jpg`
- `haaland_arms_up.jpg`
- `saka_sad.jpg`

### `managers/`
- `hansi_flick_angry.jpg`
- `hansi_flick_smiling.jpg`
- `real_madrid_manager_worried.jpg`
- `simeone_angry.jpg`

### `memes/`
- `referee_var_screen.jpg`
- `camp_nou_night.jpg`

After dropping images in, run:

```bash
python run_post.py --sync-catalog
```

This updates `assets/catalog.json` (which you can optionally edit to add even more specific descriptions) and prints all detected images. Remember to `git add assets/` and push to Heroku so Heroku has your image folder!
