# GMaps Scraper

Extract data from Google Maps saved lists and individual place pages.

The scraper fetches Google Maps URLs, reads runtime data or the rendered place
panel, and returns structured JSON.

## Requirements

- Python `3.14`
- `uv`
- `curl_cffi` for the primary fetch path
- `cloakbrowser` for browser mode and HTTP fallback

## Install

This project is intended to be consumed directly from source rather than from PyPI.

```bash
uv add git+https://github.com/michaelmwu/gmaps-scraper.git
```

If you vendor the package, also add the runtime dependency:

```bash
uv add curl-cffi cloakbrowser
```

## CLI

The package installs a `gmaps-scraper` command.

Basic usage:

```bash
uv run gmaps-scraper "https://maps.app.goo.gl/MG2Vd5pWBkL7hXL18"
```

Scrape a place page:

```bash
uv run gmaps-scraper \
  "https://www.google.com/maps/place/Den/@35.6731762,139.7127216,17z" \
  --kind place
```

Explicit fetch modes:

```bash
uv run gmaps-scraper URL --fetch-mode auto
uv run gmaps-scraper URL --fetch-mode curl
uv run gmaps-scraper URL --fetch-mode browser
```

Mode behavior:

- `auto` uses `curl_cffi` first and falls back to the browser if parsing fails
- `curl` uses only the HTTP path
- `browser` uses only the browser path

Write JSON to a file:

```bash
uv run gmaps-scraper \
  "https://maps.app.goo.gl/MG2Vd5pWBkL7hXL18" \
  --output saved-list.json
```

Run with a visible browser for debugging:

```bash
uv run gmaps-scraper \
  "https://maps.app.goo.gl/MG2Vd5pWBkL7hXL18" \
  --headed
```

Available CLI options:

- `--kind {list,place}` selects which scraper to run
- `--output PATH` writes the JSON result to a file
- `--headed` runs the browser in headed mode
- `--fetch-mode {auto,curl,browser}` selects the transport path
- `--timeout-ms INTEGER` controls the navigation timeout
- `--settle-ms INTEGER` adds extra wait time after the page loads

## Library Usage

Import the package directly in application code:

```python
from gmaps_scraper import scrape_place, scrape_saved_list

result = scrape_saved_list("https://maps.app.goo.gl/MG2Vd5pWBkL7hXL18")
place = scrape_place("https://www.google.com/maps/place/Den/@35.6731762,139.7127216,17z")

print(result.list_id)
print(result.resolved_url)
print(result.title)
print(result.to_dict())
print(place.review_count)
```

Public top-level imports intended for consumers:

- `scrape_saved_list`
- `scrape_place`
- `parse_saved_list_artifacts`
- `SavedList`
- `Place`
- `PlaceDetails`
- `ParseError`
- `ScrapeError`

## Output

The scraper returns a `SavedList` object. `to_dict()` produces JSON like this:

```json
{
  "source_url": "https://maps.app.goo.gl/MG2Vd5pWBkL7hXL18",
  "resolved_url": "https://www.google.com/maps/@30.5370705,125.4120472,6z/data=!4m3!11m2!2sUGEPbA20Qd-OH4uoWjmDgQ!3e3?entry=ttu",
  "list_id": "UGEPbA20Qd-OH4uoWjmDgQ",
  "title": "Tokyo Dinners",
  "description": "Best spots in the city",
  "places": [
    {
      "name": "Yakumo",
      "address": "Shibuya, Tokyo",
      "note": "Delicious wonton ramen. You can ask for a mix of white and dark broth.",
      "is_favorite": true,
      "lat": 35.6501307,
      "lng": 139.6868459,
      "maps_url": "https://maps.google.com/?cid=7451636382641713350"
    }
  ]
}
```

`source_url` preserves the caller's input URL. `resolved_url` captures the final URL
after redirects, which is useful for short `maps.app.goo.gl` links.

For place pages, the scraper returns a `PlaceDetails` object with fields such as
`name`, `category`, `rating`, `review_count`, `address`, `status`, `website`,
`phone`, `plus_code`, and coordinates when available.

## Behavior Notes

- Saved lists default to `curl_cffi` against Google Maps' preloaded XSSI endpoints.
- Place pages currently use the browser path and extract review metadata from the
  rendered DOM.
- Browser automation remains available for debugging, consent flows, and fallback.
- Parsing is defensive and tolerates partial metadata, but Google can change its runtime
  schema at any time.
- The parser prefers the explicit placelist ID from the resolved URL when available.

## Development

Install the dev environment:

```bash
uv sync --dev
```

Run the quality gates:

```bash
./scripts/lint.sh
./scripts/typecheck.sh
```

Run tests:

```bash
uv run python -m unittest discover -s tests
```
