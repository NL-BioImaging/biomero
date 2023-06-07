from setuptools import setup

setup(name="omero_slurm_client",
      use_scm_version=True,
      setup_requires=['setuptools_scm'],
      install_requires=[
            # "requests==2.31.0", # needs Python3.7+, which will use the toml instead
            "requests==2.27.1",
            "fabric==3.1.0",
            "paramiko==3.2.0",
            "importlib_resources>=5.4.0"
      ])