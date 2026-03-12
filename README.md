# Google Saved List Scraper

Python project for extracting places from Google Maps saved lists.

## Goal

Given a Google Maps saved-list URL, load the page in a browser context, recover the placelist payload from runtime state, and return structured place data.

## Tooling

- Language: Python
- Runner and dependency management: `uv`
- Preferred command style: `uv run python ...`
- Target version: Python `3.14`
- Current stable patch: Python `3.14.3` as of March 12, 2026

## Development

Install the dev environment:

```bash
uv sync --dev
```

Run lint and type checking:

```bash
./scripts/lint.sh
./scripts/typecheck.sh
```

Run the scraper:

```bash
uv run google-saved-lists "https://www.google.com/maps/@.../data=!4m3!11m2!2sLIST_ID!3e3"
```

## Planned Pipeline

1. Extract the placelist ID from the saved-list URL when possible.
2. Open the page in a real browser environment to avoid the incomplete HTML returned to simple HTTP clients.
3. Read `APP_INITIALIZATION_STATE` or equivalent runtime state.
4. Find the placelist payload inside the runtime object tree.
5. Remove any XSSI prefix, unescape the payload, and decode it as JSON.
6. Parse list metadata and place entries into a stable JSON result.

## Parsing Strategy

### Primary signal

The strongest identifier is the placelist ID from the URL, typically the `!2s...` token.

Example:

```python
import re

match = re.search(r"!2s([^!]+)", url)
list_id = match.group(1) if match else None
```

When walking runtime strings or nested arrays, prefer candidates that contain this exact ID.

### Fallback signal

Using `maps/placelists/list/` as a fallback is a good idea.

It should be a secondary signal, not the first one:

- Exact placelist ID match is more specific and less likely to collide.
- `maps/placelists/list/` is still useful for locating the metadata node when the ID is not present in a reachable string.
- The fallback should produce candidate nodes that are validated by nearby structure, not accepted blindly.

Recommended rule:

1. Search for an exact placelist ID match.
2. If that fails, search for strings containing `maps/placelists/list/`.
3. Score or validate the surrounding node by checking for expected list metadata and place-entry structure.

## Resilient Place Detection

Avoid relying on brittle deep indexes when extracting places. A more stable approach is to detect the coordinate tuple pattern Google tends to emit:

```python
[None, None, lat, lng]
```

Once a coordinate tuple is found, inspect the enclosing structure to extract:

- place name
- address
- place identifier or CID
- coordinates

This is more resilient than binding the parser to one exact nested array layout.

## Expected Output

```json
{
  "source_url": "https://www.google.com/maps/@.../data=!4m3!11m2!2sLIST_ID!3e3",
  "list_id": "UGEPbA20Qd-OH4uoWjmDgQ",
  "title": "My list",
  "description": "Optional text",
  "places": [
    {
      "name": "Yakumo",
      "address": "Shibuya, Tokyo",
      "lat": 35.6501307,
      "lng": 139.6868459,
      "maps_url": "https://maps.google.com/?cid=7451636382641713350"
    }
  ]
}
```

## Implementation Notes

- Expect schema drift and partial data.
- Keep parsing defensive and tolerate missing fields.
- Treat the placelist URL marker as a locator, not as final proof that the enclosing array is the correct target.
- Add fixtures for both exact-ID and fallback-only cases when tests are introduced.

## Implementation

The current codebase includes:

- a CloakBrowser-backed scraper that loads the list page and collects runtime artifacts
- a parser that ranks candidate placelist nodes using the explicit list ID first and the placelist URL marker second
- coordinate-pattern-based place detection with defensive extraction of names, addresses, and CIDs
- a CLI that emits the parsed list as JSON
