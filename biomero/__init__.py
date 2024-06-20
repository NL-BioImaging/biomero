from .slurm_client import SlurmClient

try:
    import importlib.metadata
    try:
        __version__ = importlib.metadata.version(__package__)
    except importlib.metadata.PackageNotFoundError:
        __version__ = "Version not found"
except ModuleNotFoundError:  # Python 3.7
    try:
        import pkg_resources
        __version__ = pkg_resources.get_distribution(__package__).version
    except pkg_resources.DistributionNotFound:
        __version__ = "Version not found"