import os
import subprocess
from typing import Dict, List, Optional, Union

import yaml
from typing_extensions import Literal, TypeAlias, TypedDict

DAGIT_PATH = "js_modules/dagit"

# ########################
# ##### BUILDKITE STEP DATA STRUCTURES
# ########################

# Buildkite step configurations can be quite complex-- the full specifications are in the Pipelines
# -> Step Types section of the Buildkite docs:
#   https://buildkite.com/docs/pipelines/command-step
#
# The structures defined below are subsets of the full specifications that only cover the attributes
# we use. Additional keys can be added from the full spec as needed.


class CommandStep(TypedDict, total=False):
    agents: Dict[str, str]
    commands: List[str]
    depends_on: List[str]
    key: str
    label: str
    plugins: List[Dict[str, object]]
    retry: Dict[str, object]
    timeout_in_minutes: int


class GroupStep(TypedDict):
    group: str
    key: str
    steps: List["BuildkiteLeafStep"]  # no nested groups


# use alt syntax because of `async` and `if` reserved words
TriggerStep = TypedDict(
    "TriggerStep",
    {
        "trigger": str,
        "label": str,
        "async": Optional[bool],
        "build": Dict[str, object],
        "branches": Optional[str],
        "if": Optional[str],
    },
    total=False,
)

WaitStep: TypeAlias = Literal["wait"]

BuildkiteStep: TypeAlias = Union[CommandStep, GroupStep, TriggerStep, WaitStep]
BuildkiteLeafStep = Union[CommandStep, TriggerStep, WaitStep]

# ########################
# ##### FUNCTIONS
# ########################


def safe_getenv(env_var: str) -> str:
    assert env_var in os.environ, f"${env_var} must be set."
    return os.environ[env_var]


def buildkite_yaml_for_steps(steps) -> str:
    return yaml.dump(
        {
            "env": {
                "CI_NAME": "buildkite",
                "CI_BUILD_NUMBER": "$BUILDKITE_BUILD_NUMBER",
                "CI_BUILD_URL": "$BUILDKITE_BUILD_URL",
                "CI_BRANCH": "$BUILDKITE_BRANCH",
                "CI_PULL_REQUEST": "$BUILDKITE_PULL_REQUEST",
            },
            "steps": steps,
        },
        default_flow_style=False,
    )


def check_for_release() -> bool:
    try:
        git_tag = str(
            subprocess.check_output(
                ["git", "describe", "--exact-match", "--abbrev=0"], stderr=subprocess.STDOUT
            )
        ).strip("'b\\n")
    except subprocess.CalledProcessError:
        return False

    version: Dict[str, object] = {}
    with open("python_modules/dagster/dagster/version.py", encoding="utf8") as fp:
        exec(fp.read(), version)  # pylint: disable=W0122

    if git_tag == version["__version__"]:
        return True

    return False


def is_pr_and_dagit_only() -> bool:
    branch_name = safe_getenv("BUILDKITE_BRANCH")
    base_branch = safe_getenv("BUILDKITE_PULL_REQUEST_BASE_BRANCH")

    if branch_name is None or branch_name == "master" or branch_name.startswith("release"):
        return False

    try:
        pr_commit = safe_getenv("BUILDKITE_COMMIT")
        origin_base = "origin/" + base_branch
        diff_files = (
            subprocess.check_output(["git", "diff", origin_base, pr_commit, "--name-only"])
            .decode("utf-8")
            .strip()
            .split("\n")
        )
        return all(filepath.startswith(DAGIT_PATH) for (filepath) in diff_files)

    except subprocess.CalledProcessError:
        return False


def network_buildkite_container(network_name: str) -> List[str]:
    return [
        # hold onto your hats, this is docker networking at its best. First, we figure out
        # the name of the currently running container...
        "export CONTAINER_ID=`cut -c9- < /proc/1/cpuset`",
        r'export CONTAINER_NAME=`docker ps --filter "id=\${CONTAINER_ID}" --format "{{.Names}}"`',
        # then, we dynamically bind this container into the user-defined bridge
        # network to make the target containers visible...
        "docker network connect {network_name} \\${{CONTAINER_NAME}}".format(
            network_name=network_name
        ),
    ]


def connect_sibling_docker_container(
    network_name: str, container_name: str, env_variable: str
) -> List[str]:
    return [
        # Now, we grab the IP address of the target container from within the target
        # bridge network and export it; this will let the tox tests talk to the target cot.
        (
            f"export {env_variable}=`docker inspect --format "
            f"'{{{{ .NetworkSettings.Networks.{network_name}.IPAddress }}}}' "
            f"{container_name}`"
        )
    ]


def is_release_branch(branch_name: str) -> bool:
    return branch_name.startswith("release-")
