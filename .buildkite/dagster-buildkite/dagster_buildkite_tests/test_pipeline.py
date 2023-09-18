import os
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path

import git
import pytest
import yaml


@pytest.fixture(scope="session")
def temporary_directory():
    with tempfile.TemporaryDirectory() as path:
        os.chdir(path)
        yield path


@pytest.fixture(scope="session")
def python(temporary_directory):
    path = os.path.join(temporary_directory, ".venv")
    venv.create(path, with_pip=True)

    python = os.path.join(path, "bin", "python")

    yield python


@pytest.fixture(scope="session")
def dagster_repo(temporary_directory, python):
    src_path = (Path(__file__) / ".." / ".." / ".." / "..").resolve()
    dst_path = Path(temporary_directory) / "dagster"

    repo = git.Repo.clone_from(
        src_path,
        dst_path,
        filter=["tree:0"],
    )

    # Copy in our .buildkite directory so we can still test our
    # changes during local development before committing
    dagster_buildkite_src = src_path / ".buildkite"
    dagster_buildkite_dst = dst_path / ".buildkite"

    shutil.copytree(
        dagster_buildkite_src,
        dagster_buildkite_dst,
        dirs_exist_ok=True,
    )

    repo.git.add("--all")
    repo.index.commit("Begin tests")

    os.chdir(dst_path)

    subprocess.run(
        [python, "-m", "pip", "install", "-e", ".buildkite/dagster-buildkite"],
        check=True,
    )

    yield repo


@pytest.fixture
def commit(dagster_repo):
    head = dagster_repo.head.commit

    def _commit(path: Path):
        path.touch()
        dagster_repo.index.add(str(path))
        dagster_repo.index.commit(f"Change {path}")

    yield _commit

    dagster_repo.git.reset("--hard", head)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("BUILDKITE_BRANCH", "fake")
    monkeypatch.setenv("BUILDKITE_DOCKER_QUEUE", "fake")
    monkeypatch.setenv("BUILDKITE_MEDIUM_QUEUE", "fake")
    monkeypatch.setenv("BUILDKITE_WINDOWS_QUEUE", "fake")
    monkeypatch.setenv("BUILDKITE_COMMIT", "fake")
    monkeypatch.setenv("BUILDKITE_MESSAGE", "fake")
    # Limit test comparisons to only commits changed in the test
    monkeypatch.setenv("BUILDKITE_DIFF_COMMIT", "HEAD")


@pytest.fixture
def dagster_buildkite(env, python, dagster_repo):
    executable = python.replace("python", "dagster-buildkite")

    yield lambda: PipelineSummary(
        yaml.safe_load(
            subprocess.run(
                executable,
                capture_output=True,
            ).stdout
        )
    )


@pytest.fixture
def libraries(dagster_repo):
    return [
        library.parts[-1]
        for library in (Path(dagster_repo.working_tree_dir) / "python_modules" / "libraries").glob(
            "*"
        )
        if library.is_dir()
    ]


class StepSummary:
    def __init__(self, step):
        self.name = step.get("label")
        self.skipped = bool(step.get("skip"))
        self.skip_reason = step.get("skip")

    def __repr__(self):
        return self.name


class PipelineSummary:
    def __init__(self, pipeline):
        planned = []
        skipped = []

        steps = pipeline["steps"]
        for step in steps:
            if step.get("label"):
                output = StepSummary(step)
                if output.skipped:
                    skipped.append(output)
                else:
                    planned.append(output)
            elif step.get("steps"):
                output = PipelineSummary(step)
                planned.extend(output.planned)
                skipped.extend(output.skipped)
            else:
                raise

        self.planned = planned
        self.skipped = skipped


def test_release_branch(dagster_buildkite, monkeypatch):
    monkeypatch.setenv("BUILDKITE_BRANCH", "release-0.0.1")

    output = dagster_buildkite()

    # Nothing gets skipped
    assert not output.skipped

    # We test multiple python versions
    assert any(["3.8" in step.name for step in output.planned])
    assert any(["3.10" in step.name for step in output.planned])


@pytest.mark.xfail(reason="dagster-airflow is running 3.8 tests", strict=True)
def test_main_branch(dagster_buildkite, monkeypatch):
    monkeypatch.setenv("BUILDKITE_BRANCH", "master")

    output = dagster_buildkite()

    # We only test latest python versions
    assert not any(["3.8" in step.name for step in output.planned])
    assert any(["3.10" in step.name for step in output.planned])

    # Only our test-project builds are skipped
    assert all(["test-project" in step.name for step in output.skipped])


def test_python_change_no_dependencies(dagster_repo, dagster_buildkite, libraries, commit):
    commit(
        Path(dagster_repo.working_tree_dir)
        / "python_modules"
        / "libraries"
        / "dagster-twilio"
        / "change.py"
    )

    output = dagster_buildkite()

    # The only python package test suite we run is dagster-twilio because
    # nothing depends on it.
    assert any([":pytest: dagster-twilio" in step.name for step in output.planned])
    assert not any([":pytest: dagster-twilio" in step.name for step in output.skipped])

    for library in libraries:
        if library != "dagster-twilio":
            assert not any([f":pytest: {library} " in step.name for step in output.planned])
            assert any([f":pytest: {library} " in step.name for step in output.skipped])


def test_python_change_dagster(dagster_repo, dagster_buildkite, libraries, commit):
    commit(Path(dagster_repo.working_tree_dir) / "python_modules" / "dagster" / "change.py")

    output = dagster_buildkite()

    # Every library test suite depends on dagster
    for library in libraries:
        assert any([f":pytest: {library} " in step.name for step in output.planned])
        assert not any([f":pytest: {library} " in step.name for step in output.skipped])


def test_python_change_dagster_buildkite(dagster_repo, dagster_buildkite, commit):
    assert any([":pytest: dagster-buildkite" in step.name for step in dagster_buildkite().skipped])

    commit(Path(dagster_repo.working_tree_dir) / ".buildkite" / "dagster-buildkite" / "change.py")

    assert any([":pytest: dagster-buildkite" in step.name for step in dagster_buildkite().planned])
