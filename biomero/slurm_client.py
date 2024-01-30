# -*- coding: utf-8 -*-
# Copyright 2023 Torec Luik
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from typing import Dict, List, Optional, Tuple, Any
from fabric import Connection, Result
from fabric.transfer import Result as TransferResult
from invoke.exceptions import UnexpectedExit
from paramiko import SSHException
import configparser
import re
import requests_cache
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import importlib
import logging
import time as timesleep
from string import Template
from importlib_resources import files
import io
import os

logger = logging.getLogger(__name__)


class SlurmClient(Connection):
    """A client for connecting to and interacting with a Slurm cluster over
    SSH.

    This class extends the Connection class, adding methods and
    attributes specific to working with Slurm.

    SlurmClient accepts the same arguments as Connection. Below only
    mentions the added ones:

    The easiest way to set this client up is by using a slurm-config.ini
    and the from-config() method.

    Attributes:
        slurm_data_path (str): The path to the directory containing the
            data files for Slurm jobs.
        slurm_images_path (str): The path to the directory containing
            the Singularity images for Slurm jobs.
        slurm_converters_path (str): The path to the directory containing
            the Singularity images for file converters.
        slurm_model_paths (dict): A dictionary containing the paths to
            the Singularity images for specific Slurm job models.
        slurm_model_repos (dict): A dictionary containing the git
            repositories of Singularity images for specific Slurm job models.
        slurm_model_images (dict): A dictionary containing the dockerhub
            of the Singularity images for specific Slurm job models.
            Will fill automatically from the data in the git repository,
            if you set init_slurm.
        slurm_script_path (str): The path to the directory containing
            the Slurm job submission scripts on Slurm.
        slurm_script_repo (str): The git https URL for cloning the repo
            containing the Slurm job submission scripts. Optional.

    Example:
        # Create a SlurmClient object as contextmanager

        with SlurmClient.from_config() as client:

            # Run a command on the remote host

            result = client.run('sbatch myjob.sh')

            # Check whether the command succeeded

            if result.ok:
                print('Job submitted successfully!')

            # Print the output of the command

            print(result.stdout)

    Example 2:
        # Create a SlurmClient and setup Slurm (download containers etc.)

        with SlurmClient.from_config(init_slurm=True) as client:

            client.run_workflow(...)

    """
    _DEFAULT_CONFIG_PATH_1 = "/etc/slurm-config.ini"
    _DEFAULT_CONFIG_PATH_2 = "~/slurm-config.ini"
    _DEFAULT_HOST = "slurm"
    _DEFAULT_INLINE_SSH_ENV = True
    _DEFAULT_SLURM_DATA_PATH = "my-scratch/data"
    _DEFAULT_SLURM_IMAGES_PATH = "my-scratch/singularity_images/workflows"
    _DEFAULT_SLURM_CONVERTERS_PATH = "my-scratch/singularity_images/converters"
    _DEFAULT_SLURM_GIT_SCRIPT_PATH = "slurm-scripts"
    _OUT_SEP = "--split--"
    _VERSION_CMD = "ls -h {slurm_images_path}/{image_path} | grep -oP '(?<=\-|\_)(v.+|latest)(?=.simg|.sif)'"
    # Note, grep returns exitcode 1 if no match is found!
    # This will translate into a UnexpectedExit error, so mute that if you
    # don't care about empty.
    # Like below with the "|| :".
    # Data could legit be empty.
    _DATA_CMD = "ls -h {slurm_data_path} | grep -oP '.+(?=.zip)' || :"
    _ALL_JOBS_CMD = "sacct --starttime {start_time} --endtime {end_time} --state {states} -o {columns} -n -X "
    _ZIP_CMD = "7z a -y {filename} -tzip {data_location}/data/out"
    _ACTIVE_JOBS_CMD = "squeue -u $USER --nohead --format %F"
    _JOB_STATUS_CMD = "sacct -n -o JobId,State,End -X -j {slurm_job_id}"
    # TODO move all commands to a similar format.
    # Then maybe allow overwrite from slurm-config.ini
    _LOGFILE = "omero-{slurm_job_id}.log"
    _TAIL_LOG_CMD = "tail -n {n} {log_file} | strings"
    _LOGFILE_DATA_CMD = "cat {log_file} | perl -wne '/Running [\w-]+? Job w\/ .+? \| .+? \| (.+?) \|.*/i and print$1'"

    def __init__(self,
                 host=_DEFAULT_HOST,
                 user=None,
                 port=None,
                 config=None,
                 gateway=None,
                 forward_agent=None,
                 connect_timeout=None,
                 connect_kwargs=None,
                 inline_ssh_env=_DEFAULT_INLINE_SSH_ENV,
                 slurm_data_path: str = _DEFAULT_SLURM_DATA_PATH,
                 slurm_images_path: str = _DEFAULT_SLURM_IMAGES_PATH,
                 slurm_converters_path: str = _DEFAULT_SLURM_CONVERTERS_PATH,
                 slurm_model_paths: dict = None,
                 slurm_model_repos: dict = None,
                 slurm_model_images: dict = None,
                 slurm_model_jobs: dict = None,
                 slurm_model_jobs_params: dict = None,
                 slurm_script_path: str = _DEFAULT_SLURM_GIT_SCRIPT_PATH,
                 slurm_script_repo: str = None,
                 init_slurm: bool = False,
                 ):
        """Initializes a new instance of the SlurmClient class.

        It is preferable to use #from_config(...) method to initialize
        parameters from a config file.

        Args:
            host (str, optional): The hostname or IP address of the remote
                server. Defaults to _DEFAULT_HOST.
            user (str, optional): The username to use when connecting to 
                the remote server. Defaults to None, which defaults 
                to config.user.
            port (int, optional): The SSH port to use when connecting.
                Defaults to None, which defaults to config.port.
            config (str, optional): Path to the SSH config file.
                Defaults to None, which defaults to your SSH config file.
            gateway (Connection, optional): An optional gateway for connecting 
                through a jump host. Defaults to None.
            forward_agent (bool, optional): Whether to forward the local SSH 
                agent to the remote server. Defaults to None, which 
                defaults to config.forward_agent.
            connect_timeout (int, optional): Timeout for establishing the SSH 
                connection. Defaults to None, which defaults 
                to config.timeouts.connect.
            connect_kwargs (dict, optional): Additional keyword arguments for 
                the underlying SSH connection. Handed verbatim to 
                `SSHClient.connect <paramiko.client.SSHClient.connect>`. 
                Defaults to None. 
            inline_ssh_env (bool, optional): Whether to use inline SSH
                environment. This is necessary if the remote server has 
                a restricted ``AcceptEnv`` setting (which is the common 
                default). Defaults to _DEFAULT_INLINE_SSH_ENV.
            slurm_data_path (str, optional): The path to the directory
                containing the data files for Slurm jobs.
                Defaults to _DEFAULT_SLURM_DATA_PATH.
            slurm_images_path (str, optional): The path to the directory
                containing the Singularity images for Slurm jobs.
                Defaults to _DEFAULT_SLURM_IMAGES_PATH.
            slurm_converters_path (str, optional): The path to the directory
                containing the Singularity images for file converters.
                Defaults to _DEFAULT_SLURM_CONVERTERS_PATH.
            slurm_model_paths (dict, optional): A dictionary containing the 
                paths to the Singularity images for specific Slurm job models.
                Defaults to None.
            slurm_model_repos (dict, optional): A dictionary containing the 
                git repositories of Singularity images for specific Slurm 
                job models.
                Defaults to None.
            slurm_model_images (dict, optional): A dictionary containing the 
                dockerhub of the Singularity images for specific Slurm 
                job models. Will fill automatically from the data in the git 
                repository if you set init_slurm.
                Defaults to None.
            slurm_model_jobs (dict, optional): A dictionary containing
                information about specific Slurm job models.
                Defaults to None.
            slurm_model_jobs_params (dict, optional): A dictionary containing
                parameters for specific Slurm job models.
                Defaults to None.
            slurm_script_path (str, optional): The path to the directory
                containing the Slurm job submission scripts on Slurm.
                Defaults to _DEFAULT_SLURM_GIT_SCRIPT_PATH.
            slurm_script_repo (str, optional): The git https URL for cloning
                the repo containing the Slurm job submission scripts.
                Defaults to None.
            init_slurm (bool): Whether to set up the required structures 
                on Slurm after initiating this client. This includes creating 
                missing folders, downloading container images, cloning git,etc.
                This will take a while at first but will validate your setup.
                Defaults to False to save time.
        """
        super(SlurmClient, self).__init__(host,
                                          user,
                                          port,
                                          config,
                                          gateway,
                                          forward_agent,
                                          connect_timeout,
                                          connect_kwargs,
                                          inline_ssh_env)
        self.slurm_data_path = slurm_data_path
        self.slurm_images_path = slurm_images_path
        self.slurm_converters_path = slurm_converters_path
        self.slurm_model_paths = slurm_model_paths
        self.slurm_script_path = slurm_script_path
        self.slurm_script_repo = slurm_script_repo
        self.slurm_model_repos = slurm_model_repos
        self.slurm_model_images = slurm_model_images
        self.slurm_model_jobs = slurm_model_jobs
        self.slurm_model_jobs_params = slurm_model_jobs_params

        # Init cache. Keep responses for 360 seconds
        self.cache = requests_cache.backends.sqlite.SQLiteCache(
            db_path="github_cache", use_temp=True)
        self.get_or_create_github_session()

        self.init_workflows()
        self.validate(validate_slurm_setup=init_slurm)

    def init_workflows(self, force_update: bool = False):
        """
        Retrieves the required info for the configured workflows from github.
        It will fill `slurm_model_images` with dockerhub links.

        Args:
            force_update (bool): Will overwrite already given paths
                in `slurm_model_images`

        """
        if not self.slurm_model_images:
            self.slurm_model_images = {}
        if not self.slurm_model_repos:
            logger.warning("No workflows configured!")
            self.slurm_model_repos = {}
            # skips the setup
        for workflow in self.slurm_model_repos.keys():
            if workflow not in self.slurm_model_images or force_update:
                json_descriptor = self.pull_descriptor_from_github(workflow)
                logger.debug('%s: %s', workflow, json_descriptor)
                image = json_descriptor['container-image']['image']
                self.slurm_model_images[workflow] = image

    def setup_slurm(self):
        """
        Validates or creates the required setup on the Slurm cluster.

        Raises:
            SSHException: if it cannot connect to Slurm, or runs into an error
        """
        if self.validate():
            # 1. Create directories
            dir_cmds = []
            # a. data
            if self.slurm_data_path:
                dir_cmds.append(f"mkdir -p {self.slurm_data_path}")
            # b. scripts
            if self.slurm_script_path:
                dir_cmds.append(f"mkdir -p {self.slurm_script_path}")
            # c. workflows
            if self.slurm_images_path:
                dir_cmds.append(f"mkdir -p {self.slurm_images_path}")
            r = self.run_commands(dir_cmds)
            if not r.ok:
                raise SSHException(r)

            # 2. Clone git
            if self.slurm_script_repo and self.slurm_script_path:
                # git clone into script path
                env = {
                    "REPOSRC": self.slurm_script_repo,
                    "LOCALREPO": self.slurm_script_path
                }
                cmd = 'git clone "$REPOSRC" "$LOCALREPO" 2> /dev/null || git -C "$LOCALREPO" pull'
                r = self.run_commands([cmd], env)
                if not r.ok:
                    raise SSHException(r)
            elif self.slurm_script_path:
                # generate scripts
                self.update_slurm_scripts(generate_jobs=True)

            # 3. Setup converters
            convert_cmds = []
            if self.slurm_converters_path:
                convert_cmds.append(f"mkdir -p {self.slurm_converters_path}")
            r = self.run_commands(convert_cmds)
            # copy generic job array script over to slurm
            convert_job_local = files("resources").joinpath(
                "convert_job_array.sh")
            _ = self.put(local=convert_job_local,
                         remote=self.slurm_script_path)
            # currently known converters
            # 3a. ZARR to TIFF
            # TODO extract these values to e.g. config if we have more
            convert_name = "convert_zarr_to_tiff"
            convert_py = f"{convert_name}.py"
            convert_script_local = files("resources").joinpath(
                convert_py)
            convert_def = f"{convert_name}.def"
            convert_def_local = files("resources").joinpath(
                convert_def)
            _ = self.put(local=convert_script_local,
                         remote=self.slurm_converters_path)
            _ = self.put(local=convert_def_local,
                         remote=self.slurm_converters_path)
            # Build singularity container from definition
            with self.cd(self.slurm_converters_path):
                convert_cmds = []
                if self.slurm_images_path:
                    # TODO Change the tmp dir?
                    # export SINGULARITY_TMPDIR=~/my-scratch/tmp;

                    # only if file does not exist yet
                    # convert_cmds.append(f"[ ! -f {convert_name}.sif ]")
                    # EDIT -- NO, then we can't update! Force rebuild!

                    # download /build new container
                    convert_cmds.append(
                        f"singularity build -F {convert_name}.sif {convert_def} >> sing.log 2>&1 ; echo 'finished {convert_name}.sif' &")
                r = self.run_commands(convert_cmds)

            # 4. Download workflow images
            # Create specific workflow dirs
            with self.cd(self.slurm_images_path):
                if self.slurm_model_paths:
                    modelpaths = " ".join(self.slurm_model_paths.values())
                    # mkdir cellprofiler imagej ...
                    r = self.run_commands([f"mkdir -p {modelpaths}"])
                    if not r.ok:
                        raise SSHException(r)

                if self.slurm_model_images:
                    pull_commands = []
                    for wf, image in self.slurm_model_images.items():
                        repo = self.slurm_model_repos[wf]
                        path = self.slurm_model_paths[wf]
                        _, version = self.extract_parts_from_url(repo)
                        if version == "master":
                            version = "latest"
                        pull_template = "time singularity pull --disable-cache --dir $path docker://$image:$version  >> sing.log 2>&1 ; echo 'finished $path' &"
                        t = Template(pull_template)
                        substitutes = {}
                        substitutes['path'] = path
                        substitutes['image'] = image
                        substitutes['version'] = version
                        cmd = t.safe_substitute(substitutes)
                        logger.debug(f"substituted: {cmd}")
                        pull_commands.append(cmd)
                    script_name = "pull_images.sh"
                    template_script = files("resources").joinpath(script_name)
                    with template_script.open('r') as f:
                        src = Template(f.read())
                        substitute = {'pullcommands': "\n".join(pull_commands)}
                        job_script = src.safe_substitute(substitute)
                    logger.debug(f"substituted:\n {job_script}")
                    # copy to remote file
                    full_path = self.slurm_images_path+"/"+script_name
                    _ = self.put(local=io.StringIO(job_script),
                                 remote=full_path)
                    cmd = f"time sh {script_name}"
                    r = self.run_commands([cmd])
                    if not r.ok:
                        raise SSHException(r)
                    logger.info(r.stdout)
                    # # cleanup giant singularity cache!
                    # using --disable-cache because we run in the background
                    # cmd = "singularity cache clean -f"
                    # r = self.run_commands([cmd])

        else:
            raise SSHException("Failure in connecting to Slurm cluster")

    @classmethod
    def from_config(cls, configfile: str = '',
                    init_slurm: bool = False) -> 'SlurmClient':
        """Creates a new SlurmClient object using the parameters read from a
        configuration file (.ini).

        Defaults paths to look for config files are:
            - /etc/slurm-config.ini
            - ~/slurm-config.ini

        Note that this is only for the SLURM specific values that we added.
        Most configuration values are set via configuration mechanisms from
        Fabric library,
        like SSH settings being loaded from SSH config, /etc/fabric.yml or
        environment variables.
        See Fabric's documentation for more info on configuration if needed.

        Args:
            configfile (str): The path to your configuration file. Optional.
            init_slurm (bool): Initiate / validate slurm setup. Optional
                Might take some time the first time with downloading etc.

        Returns:
            SlurmClient: A new SlurmClient object.
        """
        # Load the configuration file
        configs = configparser.ConfigParser(allow_no_value=True)
        # Loads from default locations and given location, missing files are ok
        configs.read([cls._DEFAULT_CONFIG_PATH_1,
                     cls._DEFAULT_CONFIG_PATH_2,
                     configfile])
        # Read the required parameters from the configuration file,
        # fallback to defaults
        host = configs.get("SSH", "host", fallback=cls._DEFAULT_HOST)
        inline_ssh_env = configs.getboolean(
            "SSH", "inline_ssh_env", fallback=cls._DEFAULT_INLINE_SSH_ENV)
        slurm_data_path = configs.get(
            "SLURM", "slurm_data_path", fallback=cls._DEFAULT_SLURM_DATA_PATH)
        slurm_images_path = configs.get(
            "SLURM", "slurm_images_path",
            fallback=cls._DEFAULT_SLURM_IMAGES_PATH)
        slurm_converters_path = configs.get(
            "SLURM", "slurm_converters_path",
            fallback=cls._DEFAULT_SLURM_CONVERTERS_PATH)

        # Split the MODELS into paths, repos and images
        models_dict = dict(configs.items("MODELS"))
        slurm_model_paths = {}
        slurm_model_repos = {}
        slurm_model_jobs = {}
        slurm_model_jobs_params = {}
        for k, v in models_dict.items():
            suffix_repo = '_repo'
            suffix_job = '_job'
            job_param_pattern = "(.+)_job_(.+)"
            job_param_match = re.match(job_param_pattern, k)
            if k.endswith(suffix_repo):
                slurm_model_repos[k[:-len(suffix_repo)]] = v
            elif k.endswith(suffix_job):
                slurm_model_jobs[k[:-len(suffix_job)]] = v
                slurm_model_jobs_params[k[:-len(suffix_job)]] = []
            elif job_param_match:
                print(f"Match: {slurm_model_jobs_params}")
                slurm_model_jobs_params[job_param_match.group(1)].append(
                    f" --{job_param_match.group(2)}={v}")
                print(f"Added: {slurm_model_jobs_params}")
            else:
                slurm_model_paths[k] = v

        slurm_script_path = configs.get(
            "SLURM", "slurm_script_path",
            fallback=cls._DEFAULT_SLURM_GIT_SCRIPT_PATH)
        slurm_script_repo = configs.get(
            "SLURM", "slurm_script_repo",
            fallback=None
        )
        # Create the SlurmClient object with the parameters read from
        # the config file
        return cls(host=host,
                   inline_ssh_env=inline_ssh_env,
                   slurm_data_path=slurm_data_path,
                   slurm_images_path=slurm_images_path,
                   slurm_converters_path=slurm_converters_path,
                   slurm_model_paths=slurm_model_paths,
                   slurm_model_repos=slurm_model_repos,
                   slurm_model_images=None,
                   slurm_model_jobs=slurm_model_jobs,
                   slurm_model_jobs_params=slurm_model_jobs_params,
                   slurm_script_path=slurm_script_path,
                   slurm_script_repo=slurm_script_repo,
                   init_slurm=init_slurm)

    def cleanup_tmp_files(self,
                          slurm_job_id: str,
                          filename: str,
                          data_location: str = None,
                          logfile: str = None,
                          ) -> Result:
        """
        Cleanup zip and unzipped files/folders associated with a Slurm job.

        Args:
            slurm_job_id (str): The job ID of the Slurm script.
            filename (str): The zip filename on Slurm.
            data_location (str, optional): The location of data files on Slurm.
                If not provided, it will be extracted from the log file.
            logfile (str, optional): The log file of the Slurm job. 
                If not provided, a default log file will be used.

        Returns:
            Result: The result of the cleanup operation.

        Note:
            The cleanup process involves removing the specified zip file, 
            the log file, and associated data files and folders.

        Example:
            # Cleanup temporary files for a Slurm job
            client.cleanup_tmp_files("12345", "output.zip")
        """
        cmds = []
        # zip
        rmzip = f"rm {filename}.*"
        cmds.append(rmzip)
        # log
        if logfile is None:
            logfile = self._LOGFILE
            logfile = logfile.format(slurm_job_id=slurm_job_id)
        rmlog = f"rm {logfile}"
        cmds.append(rmlog)
        # data
        if data_location is None:
            data_location = self.extract_data_location_from_log(logfile)
        rmdata = f"rm -rf {data_location} {data_location}.*"
        cmds.append(rmdata)

        try:
            result = self.run_commands(cmds)
        except UnexpectedExit as e:
            logger.warning(e)
            result = e.result
        return result or None

    def validate(self, validate_slurm_setup: bool = False):
        """Validate the connection to the Slurm cluster by running
        a simple command.

        Args:
            validate_slurm_setup (bool): Whether to also check
                and fix the Slurm setup (folders, images, etc.)

        Returns:
            bool:
                True if the validation is successful,
                False otherwise.
        """
        connected = self.run('echo " "').ok
        if connected and validate_slurm_setup:
            try:
                self.setup_slurm()
            except SSHException as e:
                logger.error(e)
                return False
        return connected

    def get_recent_log_command(self, log_file: str, n: int = 10) -> str:
        """
        Get the command to retrieve the recent log entries from a
        specified log file.

        Args:
            log_file (str): The path to the log file.
            n (int, optional): The number of recent log entries to retrieve.
                Defaults to 10.

        Returns:
            str: The command to retrieve the recent log entries.
        """
        return self._TAIL_LOG_CMD.format(n=n, log_file=log_file)

    def get_active_job_progress(self,
                                slurm_job_id: str,
                                pattern: str = r"\d+%",
                                env: Optional[Dict[str, str]] = None) -> str:
        """
        Get the progress of an active Slurm job from its logfiles.

        Args:
            slurm_job_id (str): The ID of the Slurm job.
            pattern (str): The pattern to match in the job log to extract
                the progress (default: r"\d+%").

            env (Dict[str, str], optional): Optional environment variables 
                to set when running the command. Defaults to None.

        Returns:
            str: The progress of the Slurm job.
        """
        cmdlist = []
        cmd = self.get_recent_log_command(
            log_file=self._LOGFILE.format(slurm_job_id=slurm_job_id))
        cmdlist.append(cmd)
        if env is None:
            env = {}
        try:
            result = self.run_commands(cmdlist, env=env)
        except Exception as e:
            logger.error(f"Issue with run command: {e}")
        # Match the specified pattern in the result's stdout
        try:
            latest_progress = re.findall(
                pattern, result.stdout)[-1]
        except Exception as e:
            logger.error(f"Issue with extracting progress: {e}")

        return f"Progress: {latest_progress}\n"

    def run_commands(self, cmdlist: List[str],
                     env: Optional[Dict[str, str]] = None,
                     sep: str = ' && ',
                     **kwargs) -> Result:
        """
        Run a list of shell commands consecutively on the Slurm cluster,
        ensuring the success of each before proceeding to the next.

        The environment variables can be set using the `env` argument.
        These commands retain the same session (environment variables
        etc.), unlike running them separately.

        Args:
            cmdlist (List[str]): A list of shell commands to run on Slurm.
            env (Dict[str, str], optional): Optional environment variables to
                set when running the command. Defaults to None.
            sep (str, optional): The separator used to concatenate the 
                commands. Defaults to ' && '.
            **kwargs: Additional keyword arguments.

        Returns:
            Result: The result of the last command in the list.
        """
        if env is None:
            env = {}
        cmd = sep.join(cmdlist)
        logger.info(
            f"Running commands, with env {env} and sep {sep} \
                and {kwargs}: {cmd}")
        result = self.run(cmd, env=env, **kwargs)  # out_stream=out_stream,

        try:
            # Watch out for UnicodeEncodeError when you str() this.
            logger.info(f"{result.stdout}")
        except UnicodeEncodeError as e:
            logger.error(f"Unicode error: {e}")
            # TODO: ONLY stdout RECODE NEEDED?? or also error?
            result.stdout = result.stdout.encode(
                'utf-8', 'ignore').decode('utf-8')
        return result

    def str_to_class(self, module_name: str, class_name: str, *args, **kwargs):
        """
        Return a class instance from a string reference.

        Args:
            module_name (str): The name of the module.
            class_name (str): The name of the class.
            *args: Additional positional arguments for the class constructor.
            **kwargs: Additional keyword arguments for the class constructor.

        Returns:
            object: An instance of the specified class, or None if the class or
                module does not exist.
        """
        try:
            module_ = importlib.import_module(module_name)
            try:
                class_ = getattr(module_, class_name)(*args, **kwargs)
            except AttributeError:
                logger.error('Class does not exist')
        except ImportError:
            logger.error('Module does not exist')
        return class_ or None

    def run_commands_split_out(self,
                               cmdlist: List[str],
                               env: Optional[Dict[str, str]] = None
                               ) -> List[str]:
        """Run a list of shell commands consecutively and split the output
        of each command.

        Each command in the list is executed with a separator in between
        that is unique and can be used to split
        the output of each command later. The separator used is specified
        by the `_OUT_SEP` attribute of the
        SlurmClient instance.

        Args:
            cmdlist (List[str]): A list of shell commands to run.
            env (Dict[str, str], optional): Optional environment variables 
                to set when running the command. Defaults to None.

        Returns:
            List[str]:
                A list of strings, where each string corresponds to
                the output of a single command in `cmdlist` split
                by the separator `_OUT_SEP`.

        Raises:
            SSHException: If any of the commands fail to execute successfully.
        """
        try:
            result = self.run_commands(cmdlist=cmdlist,
                                       env=env,
                                       sep=f" ; echo {self._OUT_SEP} ; ")
        except UnexpectedExit as e:
            logger.warning(e)
            result = e.result
        if result.ok:
            response = result.stdout
            split_responses = response.split(self._OUT_SEP)
            return split_responses
        else:
            # If the result is not ok, log the error and raise an SSHException
            error = f"Result is not ok: {result}"
            logger.error(error)
            raise SSHException(error)

    def list_active_jobs(self,
                         env: Optional[Dict[str, str]] = None) -> List[str]:
        """
        Get a list of active jobs from SLURM.

        Args:
            env (Dict[str, str], optional): Optional environment variables to 
                set when running the command. Defaults to None.

        Returns:
            List[str]: A list of job IDs.
        """
        # cmd = self._ACTIVE_JOBS_CMD
        cmd = self.get_jobs_info_command(start_time="now", states="r")
        logger.info("Retrieving list of active jobs from Slurm")
        result = self.run_commands([cmd], env=env)
        job_list = result.stdout.strip().split('\n')
        job_list.reverse()
        return job_list

    def list_completed_jobs(self,
                            env: Optional[Dict[str, str]] = None) -> List[str]:
        """
        Get a list of completed jobs from SLURM.

        Args:
            env (Dict[str, str], optional): Optional environment variables to
                set when running the command. Defaults to None.

        Returns:
            List[str]: A list of job IDs.
        """

        cmd = self.get_jobs_info_command(states="cd")
        logger.info("Retrieving a list of completed jobs from Slurm")
        result = self.run_commands([cmd], env=env)
        job_list = [job.strip() for job in result.stdout.strip().split('\n')]
        job_list.reverse()
        return job_list

    def list_all_jobs(self, env: Optional[Dict[str, str]] = None) -> List[str]:
        """
        Get a list of all jobs from SLURM.

        Args:
            env (Dict[str, str], optional): Optional environment variables 
                to set when running the command. Defaults to None.

        Returns:
            List[str]: A list of job IDs.
        """

        cmd = self.get_jobs_info_command()
        logger.info("Retrieving a list of all jobs from Slurm")
        result = self.run_commands([cmd], env=env)
        job_list = result.stdout.strip().split('\n')
        job_list.reverse()
        return job_list

    def get_jobs_info_command(self, start_time: str = "2023-01-01",
                              end_time: str = "now",
                              columns: str = "JobId",
                              states: str = "r,cd,f,to,rs,dl,nf") -> str:
        """Return the Slurm command to retrieve information about old jobs.

        The command will be formatted with the specified start time, which is
        expected to be in the ISO format "YYYY-MM-DD".
        The command will use the "sacct" tool to query the
        Slurm accounting database for jobs that started on or after the
        specified start time, and will output only the job IDs (-o JobId)
        without header or trailer lines (-n -X).

        Args:
            start_time (str): The start time from which to retrieve job
                information. Defaults to "2023-01-01".
            end_time (str): The end time until which to retrieve job
                information. Defaults to "now".
            columns (str): The columns to retrieve from the job information.
                Defaults to "JobId". It is comma separated, e.g. "JobId,State".
            states (str): The job states to include in the query.
                Defaults to "r,cd,f,to,rs,dl,nf".

        Returns:
            str:
                A string representing the Slurm command to retrieve
                information about old jobs.
        """
        return self._ALL_JOBS_CMD.format(start_time=start_time,
                                         end_time=end_time,
                                         states=states,
                                         columns=columns)

    def transfer_data(self, local_path: str) -> Result:
        """
        Transfers a file or directory from the local machine to the remote
        Slurm cluster.

        Args:
            local_path (str): The local path to the file or directory to
                transfer.

        Returns:
            Result: The result of the file transfer operation.
        """
        logger.info(
            f"Transfering file {local_path} to {self.slurm_data_path}")
        return self.put(local=local_path, remote=self.slurm_data_path)

    def unpack_data(self, zipfile: str,
                    env: Optional[Dict[str, str]] = None) -> Result:
        """
        Unpacks a zipped file on the remote Slurm cluster.

        Args:
            zipfile (str): The name of the zipped file to be unpacked.

            env (Dict[str, str], optional): Optional environment variables 
                to set when running the command. Defaults to None.

        Returns:
            Result: The result of the command.
        """
        cmd = self.get_unzip_command(zipfile)
        logger.info(f"Unpacking {zipfile} on Slurm")
        return self.run_commands([cmd], env=env)

    def generate_slurm_job_for_workflow(self,
                                        workflow: str,
                                        substitutes: Dict[str, str],
                                        template: str = "job_template.sh"
                                        ) -> str:
        """
        Generate a Slurm job script for a specific workflow.

        Args:
            workflow (str): The name of the workflow.
            substitutes (Dict[str, str]): A dictionary containing key-value
                pairs for substituting placeholders in the job template.
            template (str, optional): The filename of the job template.
                Defaults to "job_template.sh".

        Returns:
            str: The generated Slurm job script as a string.
        """
        # add workflow to substitutes
        substitutes['jobname'] = workflow
        # grab job template
        template_f = files("resources").joinpath(template)
        with template_f.open('r') as f:
            src = Template(f.read())
            job_script = src.safe_substitute(substitutes)
        return job_script

    def workflow_params_to_subs(self, params) -> Dict[str, str]:
        """
        Convert workflow parameters to substitution dictionary for job script.

        Args:
            params: A dictionary containing workflow parameters.

        Returns:
            Dict[str, str]: A dictionary with parameter names as keys and
                corresponding flags with placeholders as values.
        """
        subs = {}
        flags = []
        for _, param in params.items():
            flag = param['cmd_flag']
            flag = flag + " $" + param['name'].upper()
            flags.append(flag)
        subs['PARAMS'] = " ".join(flags)
        return subs

    def update_slurm_scripts(self,
                             generate_jobs: bool = False,
                             env: Optional[Dict[str, str]] = None) -> Result:
        """
        Updates the local copy of the Slurm job submission scripts.

        This function pulls the latest version of the scripts from the Git
        repository and copies them to the slurm_script_path directory.
        Alternatively, it can generate scripts from a template. This is the
        default behavior if no Git repo is provided or can be forced via the
        `generate_jobs` parameter.

        Args:
            generate_jobs (bool): Whether to generate new slurm job scripts
                INSTEAD (of pulling from git). Defaults to False, except
                if no slurm_script_repo is configured.
            env (Dict[str, str], optional): Optional environment variables 
                to set when running the command. Defaults to None.

        Returns:
            Result: The result of the command.
        """
        if not self.slurm_script_repo:
            generate_jobs = True

        if generate_jobs:
            logger.info("Generating Slurm job scripts")
            for wf, job_path in self.slurm_model_jobs.items():
                # generate job script
                params = self.get_workflow_parameters(wf)
                subs = self.workflow_params_to_subs(params)
                job_script = self.generate_slurm_job_for_workflow(wf, subs)
                # ensure all dirs exist remotely
                full_path = self.slurm_script_path+"/"+job_path
                job_dir, _ = os.path.split(full_path)
                self.run(f"mkdir -p {job_dir}")
                # copy to remote file
                result = self.put(local=io.StringIO(job_script),
                                  remote=full_path)
        else:
            cmd = self.get_update_slurm_scripts_command()
            logger.info("Updating Slurm job scripts on Slurm")
            result = self.run_commands([cmd], env=env)
        return result

    def run_workflow(self,
                     workflow_name: str,
                     workflow_version: str,
                     input_data: str,
                     email: Optional[str] = None,
                     time: Optional[str] = None,
                     **kwargs
                     ) -> Tuple[Result, int]:
        """
        Run a specified workflow on Slurm using the given parameters.

        Args:
            workflow_name (str): Name of the workflow to execute.
            workflow_version (str): Version of the workflow (image version 
                on Slurm).
            input_data (str): Name of the input data folder containing input
                image files.
            email (str, optional): Email address for Slurm job notifications.
            time (str, optional): Time limit for the Slurm job in the 
                format HH:MM:SS.
            **kwargs: Additional keyword arguments for the workflow.

        Returns:
            Tuple[Result, int]:
                A tuple containing the result of starting the workflow job and
                the Slurm job ID, or -1 if the job ID could not be extracted.

        Note:
            The Slurm job ID is extracted from the result of the 
            `run_commands` method.
        """
        sbatch_cmd, sbatch_env = self.get_workflow_command(
            workflow_name, workflow_version, input_data, email, time, **kwargs)
        print(f"Running {workflow_name} job on {input_data} on Slurm:\
            {sbatch_cmd} w/ {sbatch_env}")
        logger.info(f"Running {workflow_name} job on {input_data} on Slurm")
        res = self.run_commands([sbatch_cmd], sbatch_env)
        return res, self.extract_job_id(res)

    def extract_job_id(self, result: Result) -> int:
        """
        Extract the Slurm job ID from the result of a command.

        Args:
            result (Result): The result of a command execution.

        Returns:
            int:
                The Slurm job ID extracted from the result,
                or -1 if not found.
        """
        slurm_job_id = next((int(s.strip()) for s in result.stdout.split(
                            "Submitted batch job") if s.strip().isdigit()), -1)
        return slurm_job_id

    def get_update_slurm_scripts_command(self) -> str:
        """Generate the command to update the Git repository containing
        the Slurm scripts, if necessary.

        Returns:
            str:
                A string containing the Git command
                to update the Slurm scripts.
        """
        update_cmd = f"git -C {self.slurm_script_path} pull"
        return update_cmd

    def check_job_status(self,
                         slurm_job_ids: List[int],
                         env: Optional[Dict[str, str]] = None
                         ) -> Tuple[Dict[int, str], Result]:
        """
        Check the status of Slurm jobs with the given job IDs.

        Args:
            slurm_job_ids (List[int]): The job IDs of the Slurm jobs to check.
            env (Dict[str, str], optional): Optional environment variables to
                set when running the command. Defaults to None.

        Returns:
            Tuple[Dict[int, str], Result]:
                A tuple containing the status per input ID and the result of 
                the command execution.

        Raises:
            SSHException: If the command execution fails or no response is
                received after multiple retries.
        """
        cmd = self.get_job_status_command(slurm_job_ids)
        logger.info(f"Getting status of {slurm_job_ids} on Slurm")
        retry_status = 0
        while retry_status < 3:
            result = self.run_commands([cmd], env=env)
            logger.info(result)
            if result.ok:
                if not result.stdout:
                    # wait for 3 seconds before checking again
                    timesleep.sleep(3)
                    # retry
                    retry_status += 1
                    logger.debug(
                        f"Retry {retry_status} getting status \
                            of {slurm_job_ids}!")
                else:
                    job_status_dict = {int(line.split()[0]): line.split(
                    )[1] for line in result.stdout.split("\n") if line}
                    logger.debug(f"Job statuses: {job_status_dict}")
                    return job_status_dict, result
            else:
                error = f"Result is not ok: {result}"
                logger.error(error)
                raise SSHException(error)
        else:
            error = f"Error: Retried {retry_status} times to get \
                status of {slurm_job_ids}, but no response."
            logger.error(error)
            raise SSHException(error)

    def get_job_status_command(self, slurm_job_ids: List[int]) -> str:
        """
        Return the Slurm command to get the status of jobs with the given
        job IDs.

        Args:
            slurm_job_ids (List[int]): The job IDs of the jobs to check.

        Returns:
            str: 
                The Slurm command to get the status of the jobs.
        """
        # concat multiple jobs if needed
        slurm_job_id = " -j ".join([str(id) for id in slurm_job_ids])
        return self._JOB_STATUS_CMD.format(slurm_job_id=slurm_job_id)

    def extract_data_location_from_log(self, slurm_job_id: str = None,
                                       logfile: str = None) -> str:
        """Read SLURM job logfile to find location of the data.

        One of the parameters is required, either id or file.

        Args:
            slurm_job_id (str): Id of the slurm job
            logfile (str): Path to the logfile

        Returns:
            str: Data location according to the log

        Raises:
            SSHException: If there is an issue with the command execution.
        """
        if logfile is None and slurm_job_id is not None:
            logfile = self._LOGFILE
            logfile = logfile.format(slurm_job_id=slurm_job_id)
        cmd = self._LOGFILE_DATA_CMD.format(log_file=logfile)
        result = self.run_commands([cmd])
        if result.ok:
            return result.stdout
        else:
            raise SSHException(result)

    def get_workflow_parameters(self,
                                workflow: str) -> Dict[str, Dict[str, Any]]:
        """
        Retrieve the parameters of a workflow.

        Args:
            workflow (str): The workflow for which to retrieve the parameters.

        Returns:
            Dict[str, Dict[str, Any]]:
                A dictionary containing the workflow parameters.

        Raises:
            ValueError: If an error occurs while retrieving the workflow
                parameters.
        """
        json_descriptor = self.pull_descriptor_from_github(workflow)
        # convert to omero types
        logger.debug(json_descriptor)
        workflow_dict = {}
        for input in json_descriptor['inputs']:
            # filter cytomine parameters
            if not input['id'].startswith('cytomine'):
                workflow_params = {}
                workflow_params['name'] = input['id']
                workflow_params['default'] = input['default-value']
                workflow_params['cytype'] = input['type']
                workflow_params['optional'] = input['optional']
                cmd_flag = input['command-line-flag']
                cmd_flag = cmd_flag.replace("@id", input['id'])
                workflow_params['cmd_flag'] = cmd_flag
                workflow_params['description'] = input['description']
                workflow_dict[input['id']] = workflow_params
        return workflow_dict

    def convert_cytype_to_omtype(self,
                                 cytype: str, _default, *args, **kwargs
                                 ) -> Any:
        """
        Convert a Cytomine type to an OMERO type and instantiates it
        with args/kwargs.

        Note that Cytomine has a Python Client, and some conversion methods
        to python types, but nothing particularly worth depending on that
        library for yet. Might be useful in the future perhaps.
        (e.g. https://github.com/Cytomine-ULiege/Cytomine-python-client/
        blob/master/cytomine/cytomine_job.py)

        Args:
            cytype (str): The Cytomine type to convert.
            _default: The default value. Required to distinguish between float
                and int.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.

        Returns:
            Any:
                The converted OMERO type class instance
                or None if errors occured.

        """
        # TODO make Enum ?
        if cytype == 'Number':
            if isinstance(_default, float):
                # float instead
                return self.str_to_class("omero.scripts", "Float",
                                         *args, **kwargs)
            else:
                return self.str_to_class("omero.scripts", "Int",
                                         *args, **kwargs)
        elif cytype == 'Boolean':
            return self.str_to_class("omero.scripts", "Bool",
                                     *args, **kwargs)
        elif cytype == 'String':
            return self.str_to_class("omero.scripts", "String",
                                     *args, **kwargs)

    def extract_parts_from_url(self, input_url: str) -> Tuple[List[str], str]:
        """
        Extract the repository and branch information from the input URL.

        Args:
            input_url (str): The input GitHub URL.

        Returns:
            Tuple[List[str], str]:
                The list of url parts and the branch/version.
                If no branch is found, it will return "master"

        Raises:
            ValueError: If the input URL is not a valid GitHub URL.
        """
        url_parts = input_url.split("/")
        if len(url_parts) < 5 or url_parts[2] != "github.com":
            raise ValueError("Invalid GitHub URL")

        if "tree" in url_parts:
            # Case: URL contains a branch
            branch_index = url_parts.index("tree") + 1
            branch = url_parts[branch_index]
        else:
            # Case: URL does not specify a branch
            branch = "master"

        return url_parts, branch

    def convert_url(self, input_url: str) -> str:
        """
        Convert the input GitHub URL to an output URL that retrieves
        the 'descriptor.json' file in raw format.

        Args:
            input_url (str): The input GitHub URL.

        Returns:
            str: The output URL to the 'descriptor.json' file.

        Raises:
            ValueError: If the input URL is not a valid GitHub URL.
        """
        url_parts, branch = self.extract_parts_from_url(input_url)

        # Construct the output URL by combining the extracted information
        # with the desired file path
        output_url = f"https://github.com/{url_parts[3]}/{url_parts[4]}/raw/{branch}/descriptor.json"

        return output_url

    def pull_descriptor_from_github(self, workflow: str) -> Dict:
        """
        Pull the workflow descriptor from GitHub.

        Args:
            workflow (str): The workflow for which to pull the descriptor.

        Returns:
            Dict: The JSON descriptor.

        Raises:
            ValueError: If an error occurs while pulling the descriptor file.
        """
        git_repo = self.slurm_model_repos[workflow]
        # convert git repo to json file
        raw_url = self.convert_url(git_repo)
        logger.debug(f"Pull workflow: {workflow}: {git_repo} >> {raw_url}")
        # pull workflow params
        github_session = self.get_or_create_github_session()
        ghfile = github_session.get(raw_url)
        if ghfile.ok:
            logger.debug(f"Cached? {ghfile.from_cache}")
            json_descriptor = ghfile.json()
        else:
            raise ValueError(
                f'Error while pulling descriptor file for workflow {workflow},\
                    from {raw_url}: {ghfile.__dict__}')
        return json_descriptor

    def get_or_create_github_session(self):
        # Note, using requests_cache 1.1.1, conditional queries are default:
        # The cached response will still be used until the remote content actually changes
        # Even if the 'expire_after' is triggered. This is built into GitHub, which returns
        # a Etag in the header that only changes when the content (e.g. the descriptor) changes.
        # If you provide this Etag when querying, you will get a 304 ('no change') and it will 
        # NOT count towards your Github limits. And requests_cache does that for us now.
        # Not available in Python3.6 though. 
        s = requests_cache.CachedSession('github_cache',
                                         backend=self.cache,
                                         expire_after=1,
                                         cache_control=True
                                         )
        ## Might have bigger issues, this is related to rate limits on GitHub
        ## https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
        ## If it stays a problem, we have to add an option to add a GH key to the config
        ## E.g. see https://github.com/requests-cache/requests-cache/blob/53134ef0e99d713fed62515dfb7bcfaac5f63f9d/examples/pygithub.py
        ## Here you could have an ACCESS_TOKEN. 
        ## An ACCESS_TOKEN could improve api limits to 5000/h (from 60/h).
        retry_strategy = Retry(
            total=5,               # Maximum number of retries
            # Exponential backoff factor (delay between retries)
            backoff_factor=5,
            # Retry on these HTTP status codes
            status_forcelist=[500, 502, 503, 504, 403, 429],
            allowed_methods=frozenset(['GET'])  # Only retry for GET requests
        )
        s.mount('https://github.com/', HTTPAdapter(max_retries=retry_strategy))
        s.mount('http://github.com/', HTTPAdapter(max_retries=retry_strategy))
        return s

    def get_workflow_command(self,
                             workflow: str,
                             workflow_version: str,
                             input_data: str,
                             email: Optional[str] = None,
                             time: Optional[str] = None,
                             **kwargs) -> Tuple[str, Dict]:
        """
        Generate the Slurm workflow command and environment variables.

        Args:
            workflow (str): The name of the workflow.
            workflow_version (str): The version of the workflow.
            input_data (str): The name of the input data folder containing
                the input image files.
            email (str, optional): The email address for job notifications.
                Defaults to None, which defaults to what is in the job script.
            time (str, optional): The time limit for the job in the 
                format HH:MM:SS. Defaults to None, which defaults to what 
                is in the job script.
            **kwargs: Additional keyword arguments for the workflow.

        Returns:
            Tuple[str, Dict]:
                A tuple containing the Slurm workflow command and
                the environment variables.

        """
        model_path = self.slurm_model_paths[workflow.lower()]
        job_script = self.slurm_model_jobs[workflow.lower()]
        job_params = self.slurm_model_jobs_params[workflow.lower()]
        # grab only the image name, not the group/creator
        image = self.slurm_model_images[workflow.lower()].split("/")[1]

        sbatch_env = {
            "DATA_PATH": f"{self.slurm_data_path}/{input_data}",
            "IMAGE_PATH": f"{self.slurm_images_path}/{model_path}",
            "IMAGE_VERSION": f"{workflow_version}",
            "SINGULARITY_IMAGE": f"{image}_{workflow_version}.sif",
            "CONVERSION_PATH": f"{self.slurm_converters_path}",
            "CONVERTER_IMAGE": "convert_zarr_to_tiff.sif",
            "DO_CONVERT": "true",
            "SCRIPT_PATH": f"{self.slurm_script_path}"
        }
        workflow_env = self.workflow_params_to_envvars(**kwargs)
        env = {**sbatch_env, **workflow_env}

        email_param = "" if email is None else f" --mail-user={email}"
        time_param = "" if time is None else f" --time={time}"
        job_params.append(time_param)
        job_params.append(email_param)
        job_param = "".join(job_params)
        sbatch_cmd = f"sbatch{job_param} --output=omero-%j.log \
            {self.slurm_script_path}/{job_script}"

        return sbatch_cmd, env

    def workflow_params_to_envvars(self, **kwargs) -> Dict:
        """
        Convert workflow parameters to environment variables.

        Args:
            **kwargs: Additional keyword arguments for the workflow.

        Returns:
            Dict: A dictionary containing the environment variables.
        """
        workflow_env = {key.upper(): f"{value}" for key,
                        value in kwargs.items()}
        logger.debug(workflow_env)
        return workflow_env

    def get_cellpose_command(self, image_version: str,
                             input_data: str,
                             cp_model: str,
                             nuc_channel: int,
                             prob_threshold: float,
                             cell_diameter: float,
                             email: Optional[str] = None,
                             time: Optional[str] = None,
                             use_gpu: bool = True,
                             model: str = "cellpose") -> Tuple[str, Dict]:
        """
        Return the command and environment dictionary to run a CellPose job
        on the Slurm workload manager.
        A specific example of using the generic 'get_workflow_command'.

        Args:
            image_version (str): The version of the Singularity image to use.
            input_data (str): The name of the input data folder on the shared
                file system.
            cp_model (str): The name of the CellPose model to use.
            nuc_channel (int): The index of the nuclear channel.
            prob_threshold (float): The probability threshold for
                nuclei detection.
            cell_diameter (float): The expected cell diameter in pixels.
            email (Optional[str]): The email address to send notifications to.
                Defaults to None.
            time (Optional[str]): The maximum time for the job to run.
                Defaults to None.
            use_gpu (bool): Whether to use GPU for the CellPose job.
                Defaults to True.
            model (str): The name of the folder of the Docker image to use.
                Defaults to "cellpose".

        Returns:
            Tuple[str, dict]:
                A tuple containing the Slurm sbatch command
                and the environment dictionary.
        """
        return self.get_workflow_command(workflow=model,
                                         workflow_version=image_version,
                                         input_data=input_data,
                                         email=email,
                                         time=time,
                                         cp_model=cp_model,
                                         nuc_channel=nuc_channel,
                                         prob_threshold=prob_threshold,
                                         cell_diameter=cell_diameter,
                                         use_gpu=use_gpu)

    def copy_zip_locally(self, local_tmp_storage: str, filename: str
                         ) -> TransferResult:
        """ Copy a zip file from Slurm to the local server.

        Note about (Transfer)Result:

        Unlike similar classes such as invoke.runners.Result or
        fabric.runners.Result
        (which have a concept of “warn and return anyways on failure”)
        this class has no useful truthiness behavior.
        If a file transfer fails, some exception will be raised,
        either an OSError or an error from within Paramiko.

        Args:
            local_tmp_storage (String): Path to store the zip file locally.
            filename (String): Zip filename on Slurm.

        Returns:
            TransferResult: The result of the scp attempt.
        """
        logger.info(f"Copying zip {filename} from\
            Slurm to {local_tmp_storage}")
        return self.get(
            remote=f"{filename}.zip",
            local=local_tmp_storage)

    def zip_data_on_slurm_server(self, data_location: str, filename: str,
                                 env: Optional[Dict[str, str]] = None
                                 ) -> Result:
        """Zip the output folder of a job on Slurm

        Args:
            data_location (String): Folder on SLURM with the "data/out"
                subfolder.
            filename (String): Name to give to the zipfile.
            env (Dict[str, str], optional): Optional environment variables to 
                set when running the command. Defaults to None.

        Returns:
            Result: The result of the zip attempt.
        """
        # zip
        zip_cmd = self.get_zip_command(data_location, filename)
        logger.info(f"Zipping {data_location} as {filename} on Slurm")
        return self.run_commands([zip_cmd], env=env)

    def get_zip_command(self, data_location: str, filename: str) -> str:
        """
        Generate a command string for zipping the data on Slurm.

        Args:
            data_location (str): The folder to be zipped.
            filename (str): The name of the zip archive file to extract.
                Without extension.

        Returns:
            str: The command to create the zip file.
        """
        return self._ZIP_CMD.format(filename=filename,
                                    data_location=data_location)

    def get_logfile_from_slurm(self,
                               slurm_job_id: str,
                               local_tmp_storage: str = "/tmp/",
                               logfile: str = None
                               ) -> Tuple[str, str, TransferResult]:
        """Copy the logfile of the given SLURM job to the local server.

        Note about (Transfer)Result:

        Unlike similar classes such as invoke.runners.Result
        or fabric.runners.Result
        (which have a concept of “warn and return anyways on failure”)
        this class has no useful truthiness behavior.
        If a file transfer fails, some exception will be raised,
        either an OSError or an error from within Paramiko.

        Args:
            slurm_job_id (str): The ID of the SLURM job.
            local_tmp_storage (str, optional): Path to store the logfile 
                locally. Defaults to "/tmp/".
            logfile (str, optional): Path to the logfile on the SLURM server.
                Defaults to None.

        Returns:
            Tuple: directory, full path of the logfile, and TransferResult
        """
        if logfile is None:
            logfile = self._LOGFILE
        logfile = logfile.format(slurm_job_id=slurm_job_id)
        logger.info(f"Copying logfile {logfile} from Slurm\
            to {local_tmp_storage}")
        result = self.get(
            remote=logfile,
            local=local_tmp_storage)
        export_file = local_tmp_storage+logfile
        return local_tmp_storage, export_file, result

    def get_unzip_command(self, zipfile: str,
                          filter_filetypes: str = "*.zarr *.tiff *.tif"
                          ) -> str:
        """
        Generate a command string for unzipping a data archive and creating
        required directories for Slurm jobs.

        Args:
            zipfile (str): The name of the zip archive file to extract.
                Without extension.
            filter_filetypes (str, optional): A space-separated string
                containing the file extensions to extract from the zip file.
                E.g. defaults to "*.zarr *.tiff *.tif".
                Setting this argument to `None` will omit the file
                filter and extract all files.

        Returns:
            str:
                The command to extract the specified
                filetypes from the zip file.
        """
        unzip_cmd = f"mkdir {self.slurm_data_path}/{zipfile} \
                    {self.slurm_data_path}/{zipfile}/data \
                    {self.slurm_data_path}/{zipfile}/data/in \
                    {self.slurm_data_path}/{zipfile}/data/out \
                    {self.slurm_data_path}/{zipfile}/data/gt; \
                    7z x -y -o{self.slurm_data_path}/{zipfile}/data/in \
                    {self.slurm_data_path}/{zipfile}.zip {filter_filetypes}"

        return unzip_cmd

    def get_image_versions_and_data_files(self, model: str
                                          ) -> Tuple[List[str], List[str]]:
        """
        Retrieve the available image versions and input data files for a
        given model.

        Args:
            model (str): The name of the model to query for.

        Returns:
            Tuple[List[str], List[str]]:
                A tuple containing two lists:
                - The first list includes the available image versions, 
                    sorted in descending order.
                - The second list includes the available data files.

        Raises:
            ValueError: If the provided model is not found in the
                SlurmClient's known model paths.
        """
        image_path = self.slurm_model_paths.get(model)
        if not image_path:
            raise ValueError(
                f"No path known for provided model {model}, \
                    in {self.slurm_model_paths}")
        cmdlist = [
            self._VERSION_CMD.format(slurm_images_path=self.slurm_images_path,
                                     image_path=image_path),
            self._DATA_CMD.format(slurm_data_path=self.slurm_data_path)]
        # split responses per command
        response_list = self.run_commands_split_out(cmdlist)
        # split lines further into sublists
        response_list = [response.strip().split('\n')
                         for response in response_list]
        response_list[0] = sorted(response_list[0], reverse=True)
        return response_list[0], response_list[1]

    def get_all_image_versions_and_data_files(self
                                              ) -> Tuple[Dict[str, List[str]],
                                                         List[str]]:
        """Retrieve all available image versions and data files from
        the Slurm cluster.

        Returns:
           Tuple[Dict[str, List[str]], List[str]]:
                A tuple containing:
                - A dictionary mapping models to available versions.
                - A list of available input data folders.
        """
        resultdict = {}
        cmdlist = []

        for path in self.slurm_model_paths.values():
            pathcmd = self._VERSION_CMD.format(
                slurm_images_path=self.slurm_images_path,
                image_path=path)
            cmdlist.append(pathcmd)

        # Add data path too
        cmdlist.append(self._DATA_CMD.format(
            slurm_data_path=self.slurm_data_path))

        # Split responses per command
        response_list = self.run_commands_split_out(cmdlist)

        # Split lines further into sublists
        response_list = [response.strip().split('\n')
                         for response in response_list]

        for i, k in enumerate(self.slurm_model_paths):
            # Return highest version first
            resultdict[k] = sorted(response_list[i], reverse=True)

        return resultdict, response_list[-1]
