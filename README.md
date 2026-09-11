# Tech News Daily

A static blog built with Jekyll, hosted on GitHub Pages, that publishes a new
tech-news post automatically every day.

## How it works

- **`scripts/generate_post.py`** calls the `freeblogapi` `/generate/sync` API
  to generate a tech-news blog post, then writes it into `_posts/` as a
  properly front-matted Markdown file.
- **`.github/workflows/daily-post.yml`** runs on a daily GitHub Actions cron
  schedule, calls the script, and commits/pushes the new post. Pushing to
  `main` automatically triggers a GitHub Pages rebuild.
- The site itself is a minimal Jekyll theme (`_layouts/`, `assets/css/`).

## Setup checklist

1. **Enable GitHub Pages**: repo Settings -> Pages -> Source: "Deploy from a
   branch" -> Branch: `main` / root. GitHub will build it with Jekyll
   automatically.
2. **Verify the API schema**: open the freeblogapi Swagger docs
   (`/docs#/generation/generate_sync_v1_generate_post`) and confirm the
   request/response field names used in `scripts/generate_post.py` match the
   real API. Update `REQUEST_BODY` and the response parsing if they differ.
3. **Add API key secret (if required)**: repo Settings -> Secrets and
   variables -> Actions -> New repository secret -> name it
   `FREEBLOGAPI_KEY`.
4. **Adjust the schedule**: edit the `cron` line in
   `.github/workflows/daily-post.yml` (currently `0 7 * * *` = 07:00 UTC).
5. **Test it manually**: go to the Actions tab -> "Daily Tech News Post" ->
   "Run workflow" to trigger it on demand instead of waiting for the
   schedule.

## Local preview

```bash
bundle install
bundle exec jekyll serve
```
