from .slurm_client import SlurmClient
import importlib.metadata
try:
    __version__ = importlib.metadata.version(__package__)
except importlib.metadata.PackageNotFoundError:
    __version__ = "Version not found"
        
from .eventsourcing import *
from .views import *