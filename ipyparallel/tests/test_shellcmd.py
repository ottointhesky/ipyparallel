"""test shell command classes"""

import json
import os
import signal
import sys
import time
import warnings

import pytest

from ipyparallel.cluster.shellcmd import Platform, ShellCommandSend

windows_py_path = 'python'
linux_py_path = '/opt/conda/bin/python3'

senders = [
    (
        "windows",
        ShellCommandSend(["cmd.exe"], ["/C"], sys.executable, initialize=False),
    ),
    (
        "windows",
        ShellCommandSend(
            ["cmd.exe"], ["/C"], sys.executable, initialize=False, send_receiver_code=1
        ),
    ),
    (
        "windows",
        ShellCommandSend(
            ["powershell.exe"], ["-Command"], sys.executable, initialize=False
        ),
    ),
    (
        "windows",
        ShellCommandSend(
            ["powershell.exe"],
            ["-Command"],
            sys.executable,
            initialize=False,
            send_receiver_code=1,
        ),
    ),
    (
        "windows",
        ShellCommandSend(
            ["ssh"],
            ["-p", "2222", "ciuser@127.0.0.1"],
            windows_py_path,
            initialize=False,
        ),
    ),
    (
        "windows",
        ShellCommandSend(
            ["ssh"],
            ["-p", "2222", "ciuser@127.0.0.1"],
            windows_py_path,
            initialize=False,
            send_receiver_code=1,
        ),
    ),
    (
        "wsl",
        ShellCommandSend(
            ["bash"], ["-c"], "python3", initialize=False, send_receiver_code=1
        ),
    ),
    ("posix", ShellCommandSend(["bash"], ["-c"], "python3", initialize=False)),
    (
        "posix",
        ShellCommandSend(
            ["bash"],
            ["-c"],
            "python3",
            initialize=False,
            send_receiver_code=1,
        ),
    ),
    (
        "posix",
        ShellCommandSend(
            ["ssh"], ["-p", "2222", "ciuser@127.0.0.1"], linux_py_path, initialize=False
        ),
    ),
    (
        "posix",
        ShellCommandSend(
            ["ssh"],
            ["-p", "2222", "ciuser@127.0.0.1"],
            linux_py_path,
            initialize=False,
            send_receiver_code=1,
        ),
    ),
]

sender_ids = [
    "cmd",
    "cmd_src",
    "pwsh",
    "pwsh_src",
    "ssh-win",
    "ssh-win_src",
    "wsl",
    "bash",
    "bash_src",
    "ssh-linux",
    "ssh-linux_src",
]

# just for testing (when only running wsl test)
# del senders[0:6]
# del sender_ids[0:6]


@pytest.fixture
def shellcmd_test_cmd():
    """returns a command that runs for 5 seconds"""
    test_command = {}
    test_command["windows"] = 'ping -n 5 127.0.0.1'
    test_command["posix"] = (
        'ping -c 5 127.0.0.1'  # ping needs to be installed to the standard ubuntu docker image
    )
    return test_command


@pytest.mark.parametrize("platform, sender", senders, ids=sender_ids)
def test_shellcmds(platform, sender, shellcmd_test_cmd, ssh_running):
    def read_via_shell(shell, filename):
        # small helper function to read a file via shell commands
        if shell.platform == Platform.Windows:
            content = shell.check_output(f"type {filename}").strip()
        else:
            content = shell.check_output(f"cat {filename}").strip()
        content = content.replace("\r\n", "\n")  # correct line endings for windows
        return content.split("\n")

    def print_file(shell, filename):
        print(f"Content of file '{filename}':")
        lines = read_via_shell(sender, filename)
        for idx, l in enumerate(lines):
            print(f"{idx:3}:{l}")

    prefix = ""
    if Platform.get() == Platform.Windows:
        if platform == "wsl":
            pytest.skip("wsl deactivated")  # comment to activate wsl tests
            prefix = "/home/" + os.environ["USERNAME"] + "/"
        elif platform != "windows":
            pytest.skip("other platform")

    else:
        # posix platform
        if platform != "posix":
            pytest.skip("other platform")

    if 'ssh' in sender.shell and not ssh_running:
        pytest.skip("No ssh server running")

    # start tests

    # initialize sender class
    sender.initialize()

    if sender.breakaway_support is not None and not sender.breakaway_support:
        warnings.warn(
            "Break away process creation flag is not available (known issue for Github Windows Runners)",
            UserWarning,
            stacklevel=2,
        )

    # if Platform.get() == Platform.Windows:
    #    sender.breakaway_support = False  # just for testing

    info = sender.get_shell_info()
    assert len(info) == 2 and info[0] and info[1]

    test_cmd = shellcmd_test_cmd[str(sender.platform.name.lower())]

    python_ok = sender.has_python()
    assert python_ok is True

    test_dir = prefix + "shellcmd_test"
    test_file = "testfile.txt"

    # perform basic file/directory operations
    sender.cmd_mkdir(test_dir)
    assert sender.cmd_exists(test_dir) is True

    # create a simple text file with one line (works on all platforms)
    fullpath = test_dir + sender.pathsep + test_file
    sender.check_output(f'echo "test-line" > {fullpath}')
    assert sender.cmd_exists(fullpath) is True
    output_lines = read_via_shell(sender, fullpath)
    assert len(output_lines) == 1
    assert "test-line" in output_lines[0]

    sender.cmd_remove(fullpath)
    assert sender.cmd_exists(fullpath) is False

    sender.cmd_rmdir(test_dir)
    assert sender.cmd_exists(test_dir) is False

    # output environment (just for testing)
    # if sender.platform != Platform.Windows:
    #    pid = sender.cmd_start("env", output_file="env_output.txt")
    #    print_file(sender, "env_output.txt")

    # do start operation test
    redirect_output_file = prefix + "output.txt"
    pid = sender.cmd_start(test_cmd, output_file=redirect_output_file)
    assert pid > 0
    if not sender.cmd_running(pid):
        print_file(sender, redirect_output_file)
    # assert sender.cmd_running(pid) is True

    sender.cmd_kill(pid, signal.SIGTERM)

    if not sender.cmd_running(pid):
        print_file(sender, redirect_output_file)

    assert sender.cmd_running(pid) is False
    assert sender.cmd_exists(redirect_output_file) is True
    sender.cmd_remove(redirect_output_file)

    # do environment setting test
    # we have a dictionary of three environment entries, where the first one is empty
    ci_dict = {
        "ssh": "",
        "interface": "tcp://*",
        "registration": 60691,
        "control": 60692,
        "mux": 60693,
        "task": 60694,
        "iopub": 60695,
        "hb_ping": 60696,
        "hb_pong": 60697,
        "broadcast": [60698, 60699],
        "key": "169b682b-337c645951e7d47723061090",
        "curve_serverkey": "null",
        "location": "host",
        "pack": "json",
        "unpack": "json",
        "signature_scheme": "hmac-sha256",
    }
    env_dict = {
        "IPP_CLUSTER_ID": "",
        "IPP_PROFILE_DIR": r"~/.ipython/profile_ssh",
        "IPP_CONNECTION_INFO": json.dumps(ci_dict),
    }
    # we use a small python script to output provided environment setting
    python_cmd = "import os;"
    python_cmd += "print('IPP_CLUSTER_ID set =', 'IPP_CLUSTER_ID' in os.environ);"
    python_cmd += "print('IPP_PROFILE_DIR =',os.environ['IPP_PROFILE_DIR']);"
    python_cmd += "print('IPP_CONNECTION_INFO =',os.environ['IPP_CONNECTION_INFO'])"

    output_file = prefix + "stdout.txt"
    # pid = sender.cmd_start([sender.python_path, "-c", python_cmd], env=env_dict, output_file=output_file)
    pid = sender.cmd_start_python_code(
        python_cmd, env=env_dict, output_file=output_file
    )

    counter = 0
    max_counter = 5
    while sender.cmd_running(pid) or counter < max_counter:
        time.sleep(1)  # sleep for 1 second to make sure that command has finished
        counter += 1

    assert sender.cmd_running(pid) is False

    output_lines = read_via_shell(sender, output_file)
    # check lines if they correspond to original dictionary (env_dict)
    assert len(output_lines) == 3
    assert output_lines[0] == 'IPP_CLUSTER_ID set = False'
    for line in output_lines[1:]:
        key, value = line.split(" = ")
        assert key in env_dict
        if isinstance(env_dict[key], dict):  # convert to dictionary if needed
            value = eval(value)
        assert env_dict[key] == value
