import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_git_fd_exec_stays_on_open_repo_after_path_swap(tmp_path: Path) -> None:
    from agent_brain.memory.governance import git_fd_exec

    git = shutil.which("git")
    assert git is not None
    repo = tmp_path / "repo.git"
    victim = tmp_path / "victim.git"
    subprocess.run([git, "init", "--bare", "-q", str(repo)], check=True)
    subprocess.run([git, "init", "--bare", "-q", str(victim)], check=True)
    fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    moved = tmp_path / "moved.git"
    repo.rename(moved)
    repo.symlink_to(victim, target_is_directory=True)
    helper = Path(git_fd_exec.__file__).resolve()
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(helper),
                "--fd",
                str(fd),
                "--git",
                git,
                "--op",
                "hash-object",
            ],
            input=b"private blob",
            capture_output=True,
            pass_fds=(fd,),
            timeout=5,
        )
    finally:
        os.close(fd)

    assert result.returncode == 0
    object_id = result.stdout.strip().decode("ascii")
    assert (
        subprocess.run(
            [git, "--git-dir", str(moved), "cat-file", "-e", object_id],
            check=False,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            [git, "--git-dir", str(victim), "cat-file", "-e", object_id],
            check=False,
        ).returncode
        != 0
    )
