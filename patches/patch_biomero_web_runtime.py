"""
Compatibility patch for the OMERO.biomero web package pinned by this deployment.

Metabase dashboard links can be rendered as absolute localhost URLs when the
embedded dashboard is viewed through a reverse proxy. The BIOMERO React app
already intercepts same-host iframe links and opens them in the top window; this
patch also rewrites localhost/127.0.0.1 links to the current public origin.

Remove this when OMERO.biomero handles proxied Metabase dashboard links upstream.
"""

from pathlib import Path


PATCH_DIR = Path(__file__).resolve().parent / "patches"
OLD = (PATCH_DIR / "metabase_link_old.js").read_text(encoding="utf-8").strip()
NEW = (PATCH_DIR / "metabase_link_new.js").read_text(encoding="utf-8").strip()
BIOMERO_BUNDLE_ROOTS = [
    Path(
        "/opt/omero/web/venv3/lib/python3.12/site-packages/"
        "omero_biomero/static/omero_biomero/assets"
    ),
    Path("/opt/omero/web/OMERO.web/var/static/omero_biomero/assets"),
]


def patch_bundle(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    patched = source.replace(OLD, NEW)
    if patched == source:
        return False
    path.write_text(patched, encoding="utf-8")
    return True


def main() -> None:
    # The package asset path is present during image build. The staticfiles path
    # can exist after collection, so keep both locations in one explicit list.
    changed = 0
    for root in BIOMERO_BUNDLE_ROOTS:
        if not root.exists():
            continue
        for path in root.glob("main.*.js"):
            changed += patch_bundle(path)
    if changed == 0:
        raise RuntimeError("Could not patch OMERO.biomero web bundle")
    print(f"patched {changed} BIOMERO web bundle file(s)")


if __name__ == "__main__":
    main()