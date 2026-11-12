"""Deprecated alias of :mod:`biomero.metadata_views`.

The module renders OMERO key/value views of workflow history, so it was
renamed. This alias keeps existing imports working for one release.
"""
import warnings

from biomero.metadata_views import *  # noqa: F401,F403

warnings.warn(
    "biomero.provenance is deprecated and will be removed in the next "
    "release; import from biomero.metadata_views instead",
    DeprecationWarning,
    stacklevel=2,
)
