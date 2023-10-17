from subprocess import Popen
from subprocess import check_output
from subprocess import CREATE_NEW_CONSOLE
from subprocess import CREATE_BREAKAWAY_FROM_JOB
from subprocess import DETACHED_PROCESS
from subprocess import DEVNULL
import time


def run_process(start_cmd, flags=0, output_file=None):
    pkwargs ={}
    if output_file:
        fo = open(output_file, "w")
        pkwargs['stdout'] = fo
        pkwargs['stderr'] = fo
        pkwargs['stdin'] = DEVNULL

    p = Popen(start_cmd, close_fds=True, creationflags=flags, **pkwargs)

    print(f'__remote_pid={p.pid}__')
    return


def print_output(output_file):
    time.sleep(1)
    print(f"print of file {output_file}:")
    with open(output_file, "r") as f:
        for idx, l in enumerate(f):
            print(f"{(idx+1):3}:{l}",end="")
    print(f"End of file ({output_file})\n")


def run(idx, cmd, flags):
    f = f"output{idx}.txt"
    try:
        print(f"------------------------------------------")
        print(f"Executing command {idx}:'{cmd}' with flags={flags}")
        run_process(["cmd.exe", "/c", cmd], flags=flags, output_file=f)
        print(f"command successful!")
        print_output(f)
    except Exception as e:
        print(f"error running '{cmd}'")
        print(str(e))


run(1, "echo flag=0", flags=0)
# the following two command do not work in a github runner vm (see https://github.com/actions/runner/issues/595)
run(2, "echo flag=break", flags=CREATE_BREAKAWAY_FROM_JOB)
run(3, "echo flag=all", flags=(CREATE_NEW_CONSOLE | CREATE_BREAKAWAY_FROM_JOB))
run(4, "echo flag=detach", flags=DETACHED_PROCESS)

print("------------------------------------------")
print("Check for BREAKAWAY flag support")
cmd = ["python", "-c",
               "import subprocess; subprocess.Popen(['cmd.exe', '/C'], close_fds=True, \
               creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB);print('successful')"]
try:
    output = check_output(cmd).decode('utf8', 'replace').strip()
except Exception:
    output = ""

if output == "successful":
    print("BREAKAWAY supported")
else:
    print("BREAKAWAY NOT supported")
