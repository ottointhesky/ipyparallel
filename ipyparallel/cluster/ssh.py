#!/usr/bin/env python
"""Helper application for OS independent shell commands"""

from subprocess import check_output, CalledProcessError
from argparse import ArgumentParser
import sys


class SshShellCommandSend:
    """Wrapper for sending ssh shell commands in OS independent form"""

    def __init__(self, ssh_cmd, ssh_args, ssh_location, remote_python):
        self.ssh_cmd = ssh_cmd
        self.ssh_args = ssh_args
        self.ssh_location = ssh_location
        self.remote_python = remote_python
        self.is_linux = None    # changed if get_remote_shell_info is called

    def _check_output(self, cmd):
        return check_output(cmd).decode('utf8', 'replace')

    def _runs_successful(self, cmd):
        try:
            check_output(cmd)
        except CalledProcessError as e:
            return False
        return True

    def _as_list(self, cmd):
        if isinstance(cmd, str):
            return [cmd]
        elif isinstance(cmd, list):
            return cmd
        else:
            raise Exception("Unknown command type")

    def get_remote_shell_info(self):
        """
        get remote shell information by sending an echo command that works on all OS and shells

        :return: (str, str): string of system and shell
        """

        #  example outputs on
        #   windows-powershell: OS-WIN-CMD=%OS%;OS-WIN-PW=Windows_NT;OS-LINUX=;SHELL=
        #   windows-cmd       : "OS-WIN-CMD=Windows_NT;OS-WIN-PW=$env:OS;OS-LINUX=$OSTYPE;SHELL=$SHELL"
        #   ubuntu-bash       : OS-WIN-CMD=Windows_NT;OS-WIN-PW=:OS;OS-LINUX=linux-gnu;SHELL=/bin/bash
        #
        cmd = self.ssh_cmd + self.ssh_args + [self.ssh_location, 'echo "OS-WIN-CMD=%OS%;OS-WIN-PW=$env:OS;OS-LINUX=$OSTYPE;SHELL=$SHELL"']
        try:
            output = self._check_output(cmd)
        except CalledProcessError:
            raise Exception("Unable to get remote shell information. Are the ssh connection data correct?")
        entries = output.strip().strip('"').split(";")
        # filter output non valid entries: contains =$ or =% or has no value assign (.....=)
        valid_entries = list(filter(lambda e: not ("=$" in e or "=%" in e or e[-1] == '='), entries))
        system = shell = None

        # currently we do not check if double entries are found
        for e in valid_entries:
            key, val = e.split("=")
            if key == "OS-WIN-CMD":
                system = val
                shell = "cmd.exe"
                self.is_linux = False
            elif key == "OS-WIN-PW":
                system = val
                shell = "powershell.exe"
                self.is_linux = False
            elif key == "OS-LINUX":
                system = val
                self.is_linux = True
            elif key == "SHELL":
                shell = val

        return (system, shell)

    def check_remote_python(self, remote_python=None):
        """Check if remote python can be started"""
        if not remote_python:
            remote_python = self.remote_python
        cmd = self.ssh_cmd + self.ssh_args + [self.ssh_location, remote_python, '--version']
        return self._runs_successful(cmd)

    def check_remote_ipython_package(self):
        """Check if ipython package is installed in the remote python installation"""
        cmd = self.ssh_cmd + self.ssh_args + [self.ssh_location, self.remote_python, "-m", "pip", "show", "ipython"]
        return self._runs_successful(cmd)

    def check_output(self, cmd):
        """generic subprocess.check_output call but using the ssh connection"""
        full_cmd = self.ssh_cmd + self.ssh_args + [self.ssh_location] + self._as_list(cmd)
        return self._check_output(full_cmd)

    def check_output_python(self, cmd, contains_python_call=False):
        """generic subprocess.check_output python call using the ssh connection"""
        if not contains_python_call:
            fullcmd = self.ssh_cmd + self.ssh_args + [self.ssh_location, self.remote_python] + self._as_list(cmd)
        else:
            fullcmd = self.ssh_cmd + self.ssh_args + [self.ssh_location] + self._as_list(cmd)
        return self._check_output(fullcmd)

    def start(self, cmd, env={}, remote_output_file=None, log=None):
        """start cmd into background and return remote pid"""
        return -1

    def kill(self, pid):
        """kill remote process with the given pid"""
        pass

    def mkdir(self, p):
        """make directory recursively"""
        pass

    def exists(self, p):
        """check if file/path exists"""
        return False

    def remove(self, p):
        """delete remote file"""
        pass


class SshShellCommandReceive:
    """Wrapper for receiving and performing ssh shell commands"""
    def __init__(self):
        pass

    def start(self, start_cmd, env=None, output_file=None):
        pass

    def kill(self, pid):
        pass

    def mkdir(self, p):
        pass

    def exists(self, p):
        pass

    def remove(self, p):
        pass


def main():
    parser = ArgumentParser(description='Perform some standard shell command in a platform independent way')
    subparsers = parser.add_subparsers(dest='cmd', help='sub-command help')

    # create the parser for the "a" command
    parser_start = subparsers.add_parser('start', help='start a process into background')
    parser_start.add_argument('start_cmd', help='command that  help')
    parser_start.add_argument('--env', help='optional environment dictionary')
    parser_start.add_argument('--output_file', help='optional output redirection (for stdout and stderr)')

    parser_kill = subparsers.add_parser('kill', help='kill a process')
    parser_kill.add_argument('pid', type=int, help='pid of process that should be killed')

    parser_mkdir = subparsers.add_parser('mkdir', help='create directory recursively')
    parser_mkdir.add_argument('path', help='directory path to create')

    parser_exists = subparsers.add_parser('exists', help='checks if a file/directory exists')
    parser_exists.add_argument('path', help='path to check')

    parser_remove = subparsers.add_parser('remove', help='removes as file')
    parser_remove.add_argument('path', help='path to remove')

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()
    cmd = args.__dict__.pop('cmd')

    recevier = SshShellCommandReceive()
    if cmd == "start":
        recevier.start(**vars(args))
    elif cmd == "kill":
        recevier.kill(**vars(args))
    elif cmd == "mkdir":
        recevier.mkdir(**vars(args))
    elif cmd == "exists":
        recevier.exists(**vars(args))
    elif cmd == "remove":
        recevier.remove(**vars(args))


if __name__ == '__main__':
    main()
