# Boundary & waterbody registry

`manifest.json` lists every boundary/waterbody geometry together with its **source record** (publisher, dataset, URL,
licence, retrieval date, SHA-256 of the stored file). Status is **computed on load** (`cs/registry.py`), never typed in:

| status | meaning |
|---|---|
| `verified` | full provenance recorded, URL host is an official Indian government domain, checksum matches the stored file, geometry checks pass (waterbodies additionally: lie inside a verified pilot district) |
| `provisional` | provenance recorded but publisher is not an official government domain (third-party/derived data) |
| `unverified` | provenance incomplete, or a dependency (district) is not verified |
| `invalid` | checksum mismatch, unreadable file, implausible coordinates, empty/non-polygon geometry |

"Verified" means *source recorded + integrity + geometry checks*; it does not mean the data was re-compared with the
publisher's current release. Add data with `python -m cs.registry add-district ...` / `add-waterbody ...` (see `--help`).
Source files are stored under `sources/`. No boundary or waterbody geometry ships with the repository.
