#!/usr/bin/env python
"""
Shell helper application for OS independent shell commands

Currently, the following command are supported:
* start:   starts a process into background and returns its pid
* running: check if a process with a given pid is running
* kill:    kill a process with a given pid
* mkdir:   creates directory recursively (no error if directory already exists)
* rmdir:   remove directory recursively
* exists:  checks if a file/directory exists
* remove:  removes a file
"""

import base64
import inspect
import pathlib
import re
import shlex
import sys
import time
import warnings
from subprocess import CalledProcessError, Popen, TimeoutExpired, check_output
from tempfile import NamedTemporaryFile

from .shellcmd_receive import Platform, ShellCommandReceive, SimpleLog


class ShellCommandSend:
    """
    Helper class for sending shell commands in generic and OS independent form

    The class is designed to send shell commands to an ssh connection. Nevertheless, it can be
    used for send commands to different local shell as well, which is useful for testing. Since
    the concept uses the ipyparallel package (class ipyparallel.cluster.ShellCommandReceive) for
    executing the commands, it is necessary that a valid python installation (python_path) is provided.
    By calling has_python this can be evaluated. Furthermore, get_shell_info can be called to retrieve OS and
    shell information independent of a valid python installation.

    Since some operation require different handling for different platforms/shells the class gathers
    necessary information during initialization. This is done per default at object instantiation.
    Nevertheless, it can be postponed (initialize=False) but before any member function is called, the
    initialize function has to be called.

    Beside generic check_output[...] functions (equivalent to subprocess.check_output), the class provides
    specific shell commands which have a cmd_ prefix. When adding new functions make sure that an
    equivalent is added in the ShellCommandReceive class as well.

    To start processes through an ssh connection on a windows server that stays alife after the ssh connection
    is closed, the process must be started with the breakaway creation flag set. Which works fine on 'normal'
    machine and VMs is denied on windows github runners (I guess because of security reasons). Hence, it was
    necessary to implement a work-a-round to enable CI in github. In case breakaway is not supported, a detached
    ssh connection is started (by the ShellCommandSend object) which stays open until the process to start has
    finished. see _cmd_send for further details.

    The class supports a special send receiver mode (see self.send_receiver_code), which allows command sending
    without ipyparallel being installed on the 'other side'. In this mode basically the shellcmd_receive.py file
    is also transferred, which makes development and testing much easier.
    """

    package_name = "ipyparallel.shellcmd"  # package name for send the command
    output_template = re.compile(
        r"__([a-z][a-z0-9_]+)=([a-z0-9\-\.]+)__", re.IGNORECASE
    )
    receiver_code = pathlib.Path(
        inspect.getfile(inspect.getmodule(ShellCommandReceive))
    ).read_text()  # get full code of receiver side
    _python_chars_map = str.maketrans({"\\": "\\\\", "'": "\\'"})

    def __init__(
        self,
        shell,
        args,
        python_path,
        initialize=True,
        send_receiver_code=False,
        log=None,
    ):
        self.shell = shell
        self.args = args
        self.python_path = python_path

        # shell dependent values. Those values are determined in the initialize function
        self.shell_info = None
        self.platform = Platform.Unknown  # platform enum of shell
        self.is_powershell = None  # flag if shell is windows powershell (requires special parameter quoting)
        self.breakaway_support = None  # flag if process creation support the break_away flag (relevant for windows only; None under linux)
        self.join_params = True  # join all cmd params into a single param. does NOT work with windows cmd
        self.pathsep = (
            "/"  # equivalent to os.pathsep (will be changed during initialization)
        )

        self.send_receiver_code = (
            send_receiver_code  # should be activated when developing...
        )
        self.debugging = False  # if activated an output log (${userdir}/shellcmd.log) is created on receiver side
        self.log = None
        if log:
            if isinstance(log, str):
                self.log = SimpleLog(log)
            else:
                self.log = log

        if initialize:
            self.initialize()

    @staticmethod
    def _check_output(cmd, **kwargs):
        output = check_output(cmd, **kwargs)
        if isinstance(output, str):
            return output
        else:
            return output.decode('utf8', 'replace')

    @staticmethod
    def _runs_successful(cmd):
        try:
            check_output(cmd)
        except CalledProcessError as e:
            return False
        return True

    @staticmethod
    def _as_list(cmd):
        if isinstance(cmd, str):
            return [cmd]
        elif isinstance(cmd, list):
            return cmd
        else:
            raise TypeError(f"Unknown command type: {cmd!r}")

    @staticmethod
    def _format_for_python(param):
        if isinstance(param, str):
            return f"'{param.translate(ShellCommandSend._python_chars_map)}'"
        else:
            return str(param)

    def _cmd_send_via_shell(self, paramlist):
        # unused/deprecated function which send a command as shell command rather than as python code (see _cmd_send)
        # since it requires proper quoting for different shells, it's much more sensitive and complex than sending
        # poor python code
        def _powershell_quote(param):
            if '"' in param or "'" in param or " " in param:
                # we need to replace single and double quotes be two double quotes, but if we are inside a string,
                # we need to prepend a backslash to the double quote. Otherwise it will get removed
                quoted = ""
                in_string = False
                for idx, c in enumerate(param):
                    prev_c = None if idx == 0 else param[idx - 1]
                    next_c = None if idx == len(param) - 1 else param[idx + 1]
                    if c == '"' and prev_c != "\\":
                        in_string = not in_string
                        quoted += '"' * 2
                        continue
                    if c == "'":
                        if in_string:
                            quoted += '\\"' * 2
                        else:
                            quoted += '"' * 2
                        continue
                    quoted += c
                return "'" + quoted + "'"
            else:
                return param

        def _cmd_quote(param):
            if "'" in param:
                tmp = param.strip()  # if already double quoted we do not need to quote
                if tmp[0] == '"' and tmp[-1] == '"':
                    return tmp
                else:
                    return '"' + tmp + '"'
            else:
                return param

        if self.platform == Platform.Windows:
            if self.is_powershell:
                paramlist = [_powershell_quote(p) for p in paramlist]
            else:
                paramlist = [_cmd_quote(p) for p in paramlist]
        else:
            paramlist = [shlex.quote(p) for p in paramlist]

        full_list = [self.python_path, "-m", self.package_name] + paramlist
        if self.join_params:
            cmd = self.shell + self.args + [" ".join(full_list)]
        else:
            cmd = self.shell + self.args + full_list

        return self._check_output(cmd)

    def _cmd_start_windows_no_breakaway(self, cmd_args, py_cmd, cmd):
        # if windows platform doesn't support breakaway flag (e.g. Github Runner)
        # we need to start a detached process (as work-a-round), the runs until the
        # 'remote' process has finished. But we cannot directly start the command as detached
        # process, since redirection (for retrieving the pid) doesn't work. We need a detached
        # proxy process that redirects output the to file, that can be read by current process
        # to retrieve the pid.

        assert self.platform == Platform.Windows
        from subprocess import DETACHED_PROCESS

        tmp = NamedTemporaryFile(
            mode="w", delete=False
        )  # use python to generate a tempfile name
        fo_name = tmp.name
        tmp.close()
        fi_name = (
            fo_name + "_stdin.py"
        )  # use tempfile name as basis for python script input
        with open(fi_name, "w") as f:
            f.write(py_cmd)

        # simple python code that starts the actual cmd in a detached process
        cmd_args_str = ", ".join(f'{self._format_for_python(c)}' for c in cmd_args)
        if self.log:
            detach_log = SimpleLog("${userdir}/detach.log").filename
            tmp = str(cmd_args_str).replace("'", "")
            py_detached = (
                f"from subprocess import Popen,PIPE;fo=open(r'{fo_name}','w');"
                f"fi=open(r'{fi_name}','r');input=fi.read();del fi;"
                "from random import randint;from datetime import datetime;ranid=randint(0,999);"
                f"log=open(r'{detach_log}','a');log.write(f'{{datetime.now()}} [{{ranid}}] Popen({tmp})\\n');"
                "p=Popen(["
                + cmd_args_str
                + "], stdin=PIPE, stdout=fo, stderr=fo, universal_newlines=True);"
                "log.write(f'{{datetime.now()}} [{{ranid}}] after Popen\\n');"
                "p.stdin.write(input);p.stdin.flush();p.communicate();"
                "log.write(f'{{datetime.now()}} [{{ranid}}] after communicate\\n');"
            )
        else:
            py_detached = (
                f"from subprocess import Popen,PIPE;fo=open(r'{fo_name}','w');"
                f"fi=open(r'{fi_name}','r');input=fi.read();del fi;"
                f"p=Popen(["
                + cmd_args_str
                + "], stdin=PIPE, stdout=fo, stderr=fo, universal_newlines=True);"
                "p.stdin.write(input);p.stdin.flush();p.communicate()"
            )
        # now start proxy process detached
        # print(datetime.now(), " [py_detached] ", py_detached)
        if self.log:
            self.log.info("[ShellCommandSend._cmd_send] starting detached process...")
            self.log.debug("[ShellCommandSend._cmd_send] python command: \n" + py_cmd)
        try:
            p = Popen(
                [sys.executable, '-c', py_detached],
                close_fds=True,
                creationflags=DETACHED_PROCESS,
            )
        except Exception as e:
            if self.log:
                self.log.error(
                    f"[ShellCommandSend._cmd_send] detached process failed: {str(e)}"
                )
            raise e
        if self.log:
            self.log.info(
                "[ShellCommandSend._cmd_send] detached process started successful. Waiting for redirected output (pid)..."
            )

        # retrieve (remote) pid from output file
        output = ""
        while True:
            with open(fo_name) as f:
                output = f.read()
                if len(output) > 0:
                    break
                if p.poll() is not None:
                    if p.returncode != 0:
                        raise CalledProcessError(p.returncode, cmd)
                    else:
                        raise Exception(
                            "internal error: no pid returned, although exit code of process was 0"
                        )

            time.sleep(0.1)  # wait a 0.1s and repeat

        if self.log:
            if len(output) == 0:
                self.log.error("[ShellCommandSend._cmd_send] not output received!")
            else:
                self.log.info("[ShellCommandSend._cmd_send] output received: " + output)

        return output

    def _cmd_send(self, cmd, *args, **kwargs):
        if not self.send_receiver_code:
            preamble = "from ipyparallel.cluster.shellcmd import ShellCommandReceive\n"
        else:
            preamble = f"{self.receiver_code}\n"

        # in send receiver mode it is not required that the ipyparallel.cluster.shellcmd
        # exists (or is update to date) on the 'other' side of the shell. This is particular
        # useful when doing further development without copying the adapted file before each
        # test run. Furthermore, the calls are much faster.
        receiver_params = []
        param_str = ""

        # make sure that env is a dictionary with only str entries (for key and value; value can be null as well)
        if "env" in kwargs and kwargs["env"]:
            env = kwargs["env"]
            assert isinstance(env, dict)
            for key, value in env.items():
                if not isinstance(key, str):
                    raise TypeError(
                        f"str expected in env dict: inappropriate key type ({key!r})."
                    )
                if value and not isinstance(value, str):
                    raise TypeError(
                        f"str expected in env dict: inappropriate value type ({value!r}) for key '{key}'"
                    )

        if self.debugging:
            receiver_params.append("debugging=True")
        if self.breakaway_support is False:
            receiver_params.append("use_breakaway=False")
        if self.debugging:
            receiver_params.append("log='${userdir}/shellcmd.log'")

        py_cmd = f"{preamble}\nwith ShellCommandReceive({', '.join(receiver_params)}) as r:\n    r.{cmd}("
        for a in args:
            py_cmd += self._format_for_python(a)
        if len(kwargs) > 0:
            py_cmd += ", "
        py_cmd += ", ".join(
            f'{k}={self._format_for_python(v)}' for k, v in kwargs.items()
        )
        py_cmd += ")"
        cmd_args = self.shell + self.args + [self.python_path]
        if cmd == 'start' and self.breakaway_support is False:
            return self._cmd_start_windows_no_breakaway(cmd_args, py_cmd, cmd)
        else:
            return self._check_output(cmd_args, universal_newlines=True, input=py_cmd)

    def _get_pid(self, output):
        # need to extract pid value
        values = dict(self.output_template.findall(output))
        if 'remote_pid' in values:
            return int(values['remote_pid'])
        else:
            raise RuntimeError(f"Failed to get pid from output: {output}")

    def _check_for_break_away_flag(self):
        assert self.platform == Platform.Windows  # test only relevant for windows
        assert self.python_path is not None
        py_code = "import subprocess; subprocess.Popen(['cmd.exe', '/C'], close_fds=True, creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB); print('successful')"
        cmd = (
            self.shell
            + self.args
            + [
                self.python_path,
                '-c',
                f'"{py_code}"',
            ]
        )
        try:
            # non-zero return code (in pwsh) or empty output (in cmd), if break_away test fails
            output = self._check_output(cmd).strip()
        except Exception as e:
            # just to see which exception was thrown
            warnings.warn(
                f"Break away test exception: {e!r}",
                UserWarning,
                stacklevel=4,
            )
            output = ""

        return output == "successful"

    def initialize(self):
        """initialize necessary variables by sending an echo command that works on all OS and shells"""
        if self.shell_info:
            return
        #  example outputs on
        #   windows-powershell: OS-WIN-CMD=%OS%;OS-WIN-PW=Windows_NT;OS-LINUX=;SHELL=
        #   windows-cmd       : "OS-WIN-CMD=Windows_NT;OS-WIN-PW=$env:OS;OS-LINUX=$OSTYPE;SHELL=$SHELL"
        #   ubuntu-bash       : OS-WIN-CMD=Windows_NT;OS-WIN-PW=:OS;OS-LINUX=linux-gnu;SHELL=/bin/bash
        #   macos 11          : OS-WIN-CMD=%OS%;OS-WIN-PW=:OS;OS-LINUX=darwin20;SHELL=/bin/bash
        cmd = (
            self.shell
            + self.args
            + ['echo "OS-WIN-CMD=%OS%;OS-WIN-PW=$env:OS;OS-LINUX=$OSTYPE;SHELL=$SHELL"']
        )
        timeout = 10
        try:
            output = self._check_output(
                cmd, timeout=timeout
            )  # timeout for command is set to 10s
        except CalledProcessError as e:
            raise Exception(
                "Unable to get remote shell information. Are the ssh connection data correct?"
            )
        except TimeoutExpired as e:
            raise Exception(
                f"Timeout of {timeout}s reached while retrieving remote shell information. Are the ssh connection data correct?"
            )

        entries = output.strip().strip('\\"').strip('"').split(";")
        # filter output non valid entries: contains =$ or =% or has no value assign (.....=)
        valid_entries = list(
            filter(
                lambda e: not ("=$" in e or "=%" in e or "=:" in e or e[-1] == '='),
                entries,
            )
        )
        system = shell = None

        # currently we do not check if double entries are found
        for e in valid_entries:
            key, val = e.split("=")
            if key == "OS-WIN-CMD":
                system = val
                shell = "cmd.exe"
                self.is_powershell = False
                self.platform = Platform.Windows
                self.join_params = (
                    False  # disable joining, since it does not work for windows cmd.exe
                )
                self.pathsep = "\\"
            elif key == "OS-WIN-PW":
                system = val
                shell = "powershell.exe"
                self.is_powershell = True
                self.platform = Platform.Windows
                self.pathsep = "\\"
            elif key == "OS-LINUX":
                system = val
                self.is_powershell = False
                self.platform = Platform.Posix
            elif key == "SHELL":
                shell = val

        if self.platform == Platform.Windows and self.python_path is not None:
            self.breakaway_support = self._check_for_break_away_flag()  # check if break away flag is available (its not in windows github runners)

        self.shell_info = (system, shell)

    def get_shell_info(self):
        """
        get shell information
        :return: (str, str): string of system and shell
        """
        assert self.shell_info  # make sure that initialize was called already
        return self.shell_info

    def has_python(self, python_path=None):
        """Check if remote python can be started ('python --version')
        :return: bool: flag if python start was found
        """
        assert self.shell_info  # make sure that initialize was called already
        if not python_path:
            python_path = self.python_path
        cmd = self.shell + self.args + [python_path, '--version']
        return self._runs_successful(cmd)

    def has_ipython_package(self):
        """Check if ipython package is installed in the remote python installation"""
        assert self.shell_info  # make sure that initialize was called already
        cmd = (
            self.shell + self.args + [self.python_path, "-m", "pip", "show", "ipython"]
        )
        return self._runs_successful(cmd)

    def check_output(self, cmd, **kwargs):
        """subprocess.check_output call using the shell connection
        :param cmd: command (str or list of strs) that should be executed
        :param kwargs: additional parameters that are passed to subprocess.check_output
        :return: output of executed command"""
        assert self.shell_info  # make sure that initialize was called already
        full_cmd = self.shell + self.args + self._as_list(cmd)
        return self._check_output(full_cmd, **kwargs)

    def check_output_python_module(self, module_params, **kwargs):
        """subprocess.check_output call based on python module call
        :param module_params: python module and parameters (str or list of strs) that should be executed
        :param kwargs: additional parameters that are passed to subprocess.check_output
        :return: output of executed command
        """
        assert self.shell_info  # make sure that initialize was called already
        cmd = (
            self.shell
            + self.args
            + [self.python_path, "-m"]
            + self._as_list(module_params)
        )
        return self._check_output(cmd, **kwargs)

    def check_output_python_code(self, python_code, **kwargs):
        """subprocess.check_output call running the provided python code
        :param python_code: code that should be executed
        :param kwargs: additional parameters that are passed to subprocess.check_output
        :return: output of executed command
        """
        assert self.shell_info  # make sure that initialize was called already
        assert "input" not in kwargs  # must not be specified
        assert "universal_newlines" not in kwargs  # must not be specified
        cmd = self.shell + self.args + [self.python_path]
        return self._check_output(
            cmd, universal_newlines=True, input=python_code, **kwargs
        )

    def cmd_start(self, cmd, env=None, output_file=None):
        """starts command into background and return remote pid
        :param cmd: command (str or list of strs) that should be started
        :param env: dictionary of environment variable that should be set/unset before starting the process
        :param output_file: stdout and stderr will be redirected to the (remote) file
        :return: pid of started process
        """
        # join commands into a single parameter. otherwise
        assert self.shell_info  # make sure that initialize was called already
        if isinstance(cmd, str):
            paramlist = shlex.split(cmd)
        else:
            paramlist = self._as_list(cmd)

        return self._get_pid(
            self._cmd_send("cmd_start", paramlist, env=env, output_file=output_file)
        )

    def cmd_start_python_module(self, module_params, env=None, output_file=None):
        """start python module into background and return remote pid
        :param module_params: python module and parameters (str or list of strs) that should be executed
        :param env: dictionary of environment variable that should be set before starting the process
        :param output_file: stdout and stderr will be redirected to the (remote) file
        :return: pid of started process
        """
        assert self.shell_info  # make sure that initialize was called already
        paramlist = [self.python_path, "-m"] + self._as_list(module_params)
        return self._get_pid(
            self._cmd_send("cmd_start", paramlist, env=env, output_file=output_file)
        )

    def cmd_start_python_code(self, python_code, env=None, output_file=None):
        """start python with provided code into background and return remote pid
        :param python_code: code that should be executed
        :param env: dictionary of environment variable that should be set before starting the process
        :param output_file: stdout and stderr will be redirected to the (remote) file
        :return: pid of started process
        """
        assert self.shell_info  # make sure that initialize was called already
        # encoding the python code as base64 stream and decoding on the other side, remove any complicated
        # quoting strategy. We do not check the length of the code, which could exceed the shell parameter
        # string limit. Since the limit is typically > 2k, little code snippets should not cause any problems.
        encoded = base64.b64encode(python_code.encode())
        py_cmd = f'import base64;exec(base64.b64decode({encoded}).decode())'
        if self.platform == Platform.Windows:
            py_cmd = '"' + py_cmd + '"'
        paramlist = [self.python_path, "-c", py_cmd]
        return self._get_pid(
            self._cmd_send("cmd_start", paramlist, env=env, output_file=output_file)
        )

    def cmd_running(self, pid):
        """check if given (remote) pid is running"""
        assert self.shell_info  # make sure that initialize was called already
        output = self._cmd_send("cmd_running", pid)
        # check output
        if "__running=1__" in output:
            return True
        elif "__running=0__" in output:
            return False
        else:
            raise Exception(
                f"Unexpected output ({output}) returned from by the running shell command"
            )

    def cmd_kill(self, pid, sig=None):
        """kill (remote) process with the given pid"""
        assert self.shell_info  # make sure that initialize was called already
        if sig:
            self._cmd_send("cmd_kill", pid, sig=int(sig))
        else:
            self._cmd_send("cmd_kill", pid)

    def cmd_mkdir(self, p):
        """make directory recursively"""
        assert self.shell_info  # make sure that initialize was called already
        self._cmd_send("cmd_mkdir", p)

    def cmd_rmdir(self, p):
        """remove directory recursively"""
        assert self.shell_info  # make sure that initialize was called already
        self._cmd_send("cmd_rmdir", p)

    def cmd_exists(self, p):
        """check if file/path exists"""
        assert self.shell_info  # make sure that initialize was called already
        output = self._cmd_send("cmd_exists", p)
        # check output
        if "__exists=1__" in output:
            return True
        elif "__exists=0__" in output:
            return False
        else:
            raise Exception(
                f"Unexpected output ({output}) returned from by the exists shell command"
            )

    def cmd_remove(self, p):
        """delete remote file"""
        assert self.shell_info  # make sure that initialize was called already
        output = self._cmd_send("cmd_remove", p)


# test some test code, which can be removed later on
# connection_dict = {
#            "ssh": "",
#            "interface": "tcp://*",
#            "registration": 60691,
#            "control": 60692,
#            "mux": 60693,
#            "task": 60694,
#            "iopub": 60695,
#            "hb_ping": 60696,
#            "hb_pong": 60697,
#            "broadcast": [60698, 60699],
#            "key": "169b682b-337c645951e7d47723061090",
#            "curve_serverkey": "null",
#            "location": "host",
#            "pack": "json",
#            "unpack": "json",
#            "signature_scheme": "hmac-sha256",
# }
# env_dict = {
#        "IPP_CLUSTER_ID": "",
#        "IPP_PROFILE_DIR": r"~/.ipython/profile_ssh",
#        "IPP_CONNECTION_INFO": json.dumps(connection_dict),
# }

# import sys
# sender = ShellCommandSend(["cmd.exe"], ["/C"], sys.executable, send_receiver_class=1,log="${userdir}/shellcmd.log")
# sender.debugging = True
# sender = ShellCommandSend(["ssh"], ["-p", "2222", "ciuser@127.0.0.1"], "python", send_receiver_class=1)
# sender.break_away_support = False
# sender = ShellCommandSend(["/usr/bin/bash"], ["-c"], sys.executable, send_receiver_class=1)
# pid = sender.cmd_start_python_code( "print('hallo johannes')", output_file="output.txt" )
# pid = sender.cmd_start( ['ping', '-n', '5', '127.0.0.1'], env=env_dict) #, output_file="output.txt" )
# print(f"pid={pid}")
